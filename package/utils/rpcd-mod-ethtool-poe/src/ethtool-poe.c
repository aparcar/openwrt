// SPDX-License-Identifier: GPL-2.0-or-later
/*
 * rpcd plugin: PoE/PSE port control via the kernel ethtool netlink API.
 *
 * Registers the ubus object "poe" and talks to the kernel PSE framework
 * directly (ETHTOOL_MSG_PSE_GET / ETHTOOL_MSG_PSE_SET), without shelling
 * out to ethtool.
 *
 *   poe.info                                 realtek-poe compatible summary
 *   poe.status { "port"?: <dev> }            all PSE attributes per port
 *   poe.manage { "port": <dev>, "enable": <bool> }
 *   poe.set    { "port": <dev>, "enable"?: <bool>, "priority"?: <int>,
 *                "power_limit"?: <mW> }
 *   poe.reload                               no-op (realtek-poe compat)
 *
 * A "port" is the name of the network device the PSE is attached to.
 */

#define _GNU_SOURCE /* struct ucred for libnl-tiny */

#include <errno.h>
#include <dirent.h>
#include <string.h>
#include <net/if.h>

#include <linux/ethtool.h>
#include <linux/ethtool_netlink.h>

#include <netlink/attr.h>
#include <netlink/genl/genl.h>
#include <unl.h>

#include <libubus.h>
#include <rpcd/plugin.h>

#define PSE_MAX_RANGES	8

struct pse_range {
	uint32_t min;
	uint32_t max;
};

struct pse_info {
	/* bitmask of ETHTOOL_A_PSE_* attributes present in the reply */
	uint32_t have;
	uint32_t val[ETHTOOL_A_PSE_MAX + 1];
	struct pse_range ranges[PSE_MAX_RANGES];
	int n_ranges;
};

#define pse_has(i, a)	((i)->have & (1U << (a)))

static struct blob_buf buf;

enum {
	RPC_P_PORT,
	RPC_P_ENABLE,
	RPC_P_PRIORITY,
	RPC_P_POWER_LIMIT,
	__RPC_P_MAX,
};

static const struct blobmsg_policy rpc_poe_policy[__RPC_P_MAX] = {
	[RPC_P_PORT]        = { .name = "port",        .type = BLOBMSG_TYPE_STRING },
	[RPC_P_ENABLE]      = { .name = "enable",      .type = BLOBMSG_TYPE_BOOL },
	[RPC_P_PRIORITY]    = { .name = "priority",    .type = BLOBMSG_TYPE_INT32 },
	[RPC_P_POWER_LIMIT] = { .name = "power_limit", .type = BLOBMSG_TYPE_INT32 },
};

static int
errno_to_ubus(int err)
{
	switch (err < 0 ? -err : err) {
	case 0:
		return UBUS_STATUS_OK;
	case ENODEV:
	case ENOENT:
		return UBUS_STATUS_NOT_FOUND;
	case EOPNOTSUPP:
		return UBUS_STATUS_NOT_SUPPORTED;
	case EINVAL:
	case ERANGE:
		return UBUS_STATUS_INVALID_ARGUMENT;
	case EPERM:
	case EACCES:
		return UBUS_STATUS_PERMISSION_DENIED;
	case EBUSY:
	case EAGAIN:
		return UBUS_STATUS_TIMEOUT;
	default:
		return UBUS_STATUS_UNKNOWN_ERROR;
	}
}

static bool
port_valid(const char *dev)
{
	return dev && *dev && strlen(dev) < IFNAMSIZ && if_nametoindex(dev) > 0;
}

static struct nl_msg *
pse_msg(struct unl *unl, int cmd, const char *dev)
{
	struct nl_msg *msg;
	struct nlattr *hdr;

	msg = unl_genl_msg(unl, cmd, false);
	if (!msg)
		return NULL;

	hdr = nla_nest_start(msg, ETHTOOL_A_PSE_HEADER);
	if (!hdr || nla_put_string(msg, ETHTOOL_A_HEADER_DEV_NAME, dev)) {
		nlmsg_free(msg);
		return NULL;
	}
	nla_nest_end(msg, hdr);

	return msg;
}

static int
pse_get_cb(struct nl_msg *msg, void *arg)
{
	struct genlmsghdr *gnlh = nlmsg_data(nlmsg_hdr(msg));
	struct pse_info *info = arg;
	struct nlattr *attr, *tb[ETHTOOL_A_C33_PSE_PW_LIMIT_MAX + 1];
	int rem, type;

	nla_for_each_attr(attr, genlmsg_attrdata(gnlh, 0),
			  genlmsg_attrlen(gnlh, 0), rem) {
		type = nla_type(attr);

		if (type == ETHTOOL_A_C33_PSE_PW_LIMIT_RANGES) {
			if (info->n_ranges >= PSE_MAX_RANGES)
				continue;
			if (nla_parse_nested(tb, ETHTOOL_A_C33_PSE_PW_LIMIT_MAX,
					     attr, NULL))
				continue;
			if (!tb[ETHTOOL_A_C33_PSE_PW_LIMIT_MIN] ||
			    !tb[ETHTOOL_A_C33_PSE_PW_LIMIT_MAX])
				continue;
			info->ranges[info->n_ranges].min =
				nla_get_u32(tb[ETHTOOL_A_C33_PSE_PW_LIMIT_MIN]);
			info->ranges[info->n_ranges].max =
				nla_get_u32(tb[ETHTOOL_A_C33_PSE_PW_LIMIT_MAX]);
			info->n_ranges++;
			continue;
		}

		if (type <= ETHTOOL_A_PSE_HEADER || type > ETHTOOL_A_PSE_MAX)
			continue;
		if (nla_len(attr) < (int)sizeof(uint32_t))
			continue;

		info->val[type] = nla_get_u32(attr);
		info->have |= 1U << type;
	}

	return NL_SKIP;
}

static int
pse_get(struct unl *unl, const char *dev, struct pse_info *info)
{
	struct nl_msg *msg;

	memset(info, 0, sizeof(*info));

	msg = pse_msg(unl, ETHTOOL_MSG_PSE_GET, dev);
	if (!msg)
		return -ENOMEM;

	return unl_request(unl, msg, pse_get_cb, info);
}

static bool
pse_present(const struct pse_info *info)
{
	return pse_has(info, ETHTOOL_A_C33_PSE_ADMIN_STATE) ||
	       pse_has(info, ETHTOOL_A_PODL_PSE_ADMIN_STATE);
}

static int
pse_open(struct unl *unl)
{
	if (unl_genl_init(unl, ETHTOOL_GENL_NAME))
		return UBUS_STATUS_NOT_SUPPORTED;

	return UBUS_STATUS_OK;
}

/* --- value names ------------------------------------------------------- */

static const char *
admin_state_str(uint32_t v)
{
	/* C33 and PoDL admin state enums share the same values */
	switch (v) {
	case ETHTOOL_C33_PSE_ADMIN_STATE_DISABLED: return "disabled";
	case ETHTOOL_C33_PSE_ADMIN_STATE_ENABLED:  return "enabled";
	default:                                   return "unknown";
	}
}

static const char *
c33_pw_d_status_str(uint32_t v)
{
	switch (v) {
	case ETHTOOL_C33_PSE_PW_D_STATUS_DISABLED:   return "disabled";
	case ETHTOOL_C33_PSE_PW_D_STATUS_SEARCHING:  return "searching";
	case ETHTOOL_C33_PSE_PW_D_STATUS_DELIVERING: return "delivering";
	case ETHTOOL_C33_PSE_PW_D_STATUS_TEST:       return "test";
	case ETHTOOL_C33_PSE_PW_D_STATUS_FAULT:      return "fault";
	case ETHTOOL_C33_PSE_PW_D_STATUS_OTHERFAULT: return "otherfault";
	default:                                     return "unknown";
	}
}

static const char *
podl_pw_d_status_str(uint32_t v)
{
	switch (v) {
	case ETHTOOL_PODL_PSE_PW_D_STATUS_DISABLED:   return "disabled";
	case ETHTOOL_PODL_PSE_PW_D_STATUS_SEARCHING:  return "searching";
	case ETHTOOL_PODL_PSE_PW_D_STATUS_DELIVERING: return "delivering";
	case ETHTOOL_PODL_PSE_PW_D_STATUS_SLEEP:      return "sleep";
	case ETHTOOL_PODL_PSE_PW_D_STATUS_IDLE:       return "idle";
	case ETHTOOL_PODL_PSE_PW_D_STATUS_ERROR:      return "error";
	default:                                      return "unknown";
	}
}

static const char *
c33_ext_state_str(uint32_t v)
{
	switch (v) {
	case ETHTOOL_C33_PSE_EXT_STATE_ERROR_CONDITION:     return "error_condition";
	case ETHTOOL_C33_PSE_EXT_STATE_MR_MPS_VALID:        return "mr_mps_valid";
	case ETHTOOL_C33_PSE_EXT_STATE_MR_PSE_ENABLE:       return "mr_pse_enable";
	case ETHTOOL_C33_PSE_EXT_STATE_OPTION_DETECT_TED:   return "option_detect_ted";
	case ETHTOOL_C33_PSE_EXT_STATE_OPTION_VPORT_LIM:    return "option_vport_lim";
	case ETHTOOL_C33_PSE_EXT_STATE_OVLD_DETECTED:       return "ovld_detected";
	case ETHTOOL_C33_PSE_EXT_STATE_PD_DLL_POWER_TYPE:   return "pd_dll_power_type";
	case ETHTOOL_C33_PSE_EXT_STATE_POWER_NOT_AVAILABLE: return "power_not_available";
	case ETHTOOL_C33_PSE_EXT_STATE_SHORT_DETECTED:      return "short_detected";
	default:                                            return "unknown";
	}
}

/* realtek-poe status strings, as expected by luci-app-poe */
static const char *
compat_status_str(const struct pse_info *info)
{
	uint32_t v;

	if (pse_has(info, ETHTOOL_A_C33_PSE_PW_D_STATUS)) {
		v = info->val[ETHTOOL_A_C33_PSE_PW_D_STATUS];
		switch (v) {
		case ETHTOOL_C33_PSE_PW_D_STATUS_DISABLED:   return "Disabled";
		case ETHTOOL_C33_PSE_PW_D_STATUS_SEARCHING:  return "Searching";
		case ETHTOOL_C33_PSE_PW_D_STATUS_DELIVERING: return "Delivering power";
		case ETHTOOL_C33_PSE_PW_D_STATUS_TEST:       return "Requesting power";
		case ETHTOOL_C33_PSE_PW_D_STATUS_FAULT:      return "Fault";
		case ETHTOOL_C33_PSE_PW_D_STATUS_OTHERFAULT: return "Other fault";
		}
	} else if (pse_has(info, ETHTOOL_A_PODL_PSE_PW_D_STATUS)) {
		v = info->val[ETHTOOL_A_PODL_PSE_PW_D_STATUS];
		switch (v) {
		case ETHTOOL_PODL_PSE_PW_D_STATUS_DISABLED:   return "Disabled";
		case ETHTOOL_PODL_PSE_PW_D_STATUS_SEARCHING:
		case ETHTOOL_PODL_PSE_PW_D_STATUS_SLEEP:
		case ETHTOOL_PODL_PSE_PW_D_STATUS_IDLE:       return "Searching";
		case ETHTOOL_PODL_PSE_PW_D_STATUS_DELIVERING: return "Delivering power";
		case ETHTOOL_PODL_PSE_PW_D_STATUS_ERROR:      return "Fault";
		}
	}

	return "unknown";
}

/* --- reply builders ---------------------------------------------------- */

static void
add_u32(const struct pse_info *info, int attr, const char *name)
{
	if (pse_has(info, attr))
		blobmsg_add_u32(&buf, name, info->val[attr]);
}

static void
add_port_status(const char *dev, const struct pse_info *info)
{
	void *o, *a, *r;
	int i;

	o = blobmsg_open_table(&buf, dev);

	if (pse_has(info, ETHTOOL_A_C33_PSE_ADMIN_STATE)) {
		blobmsg_add_string(&buf, "type", "c33");
		blobmsg_add_string(&buf, "admin_state",
			admin_state_str(info->val[ETHTOOL_A_C33_PSE_ADMIN_STATE]));
	} else if (pse_has(info, ETHTOOL_A_PODL_PSE_ADMIN_STATE)) {
		blobmsg_add_string(&buf, "type", "podl");
		blobmsg_add_string(&buf, "admin_state",
			admin_state_str(info->val[ETHTOOL_A_PODL_PSE_ADMIN_STATE]));
	}

	if (pse_has(info, ETHTOOL_A_C33_PSE_PW_D_STATUS))
		blobmsg_add_string(&buf, "pw_d_status",
			c33_pw_d_status_str(info->val[ETHTOOL_A_C33_PSE_PW_D_STATUS]));
	else if (pse_has(info, ETHTOOL_A_PODL_PSE_PW_D_STATUS))
		blobmsg_add_string(&buf, "pw_d_status",
			podl_pw_d_status_str(info->val[ETHTOOL_A_PODL_PSE_PW_D_STATUS]));

	add_u32(info, ETHTOOL_A_C33_PSE_PW_CLASS, "class");
	add_u32(info, ETHTOOL_A_C33_PSE_ACTUAL_PW, "actual_power");

	if (pse_has(info, ETHTOOL_A_C33_PSE_EXT_STATE)) {
		blobmsg_add_string(&buf, "ext_state",
			c33_ext_state_str(info->val[ETHTOOL_A_C33_PSE_EXT_STATE]));
		add_u32(info, ETHTOOL_A_C33_PSE_EXT_SUBSTATE, "ext_substate");
	}

	add_u32(info, ETHTOOL_A_C33_PSE_AVAIL_PW_LIMIT, "power_limit");

	if (info->n_ranges) {
		a = blobmsg_open_array(&buf, "power_limit_ranges");
		for (i = 0; i < info->n_ranges; i++) {
			r = blobmsg_open_table(&buf, NULL);
			blobmsg_add_u32(&buf, "min", info->ranges[i].min);
			blobmsg_add_u32(&buf, "max", info->ranges[i].max);
			blobmsg_close_table(&buf, r);
		}
		blobmsg_close_array(&buf, a);
	}

	add_u32(info, ETHTOOL_A_PSE_PW_D_ID, "power_domain");
	add_u32(info, ETHTOOL_A_PSE_PRIO, "priority");
	add_u32(info, ETHTOOL_A_PSE_PRIO_MAX, "priority_max");

	blobmsg_close_table(&buf, o);
}

/* returns the port's consumption in mW, 0 if not reported */
static uint32_t
add_port_compat(const char *dev, const struct pse_info *info)
{
	uint32_t mw = 0;
	void *o;

	o = blobmsg_open_table(&buf, dev);

	blobmsg_add_u32(&buf, "priority",
		pse_has(info, ETHTOOL_A_PSE_PRIO) ? info->val[ETHTOOL_A_PSE_PRIO] : 0);
	blobmsg_add_string(&buf, "status", compat_status_str(info));

	if (pse_has(info, ETHTOOL_A_C33_PSE_PW_CLASS))
		blobmsg_add_u32(&buf, "class", info->val[ETHTOOL_A_C33_PSE_PW_CLASS]);

	if (pse_has(info, ETHTOOL_A_C33_PSE_ACTUAL_PW)) {
		mw = info->val[ETHTOOL_A_C33_PSE_ACTUAL_PW];
		blobmsg_add_double(&buf, "consumption", mw / 1000.0);
	}

	blobmsg_close_table(&buf, o);

	return mw;
}

/* --- ubus methods ------------------------------------------------------ */

typedef void (*port_cb)(const char *dev, const struct pse_info *info,
			void *priv);

/* call cb for every network device backed by a PSE controller */
static int
for_each_pse_port(struct unl *unl, port_cb cb, void *priv)
{
	struct pse_info info;
	struct dirent *e;
	DIR *d;

	d = opendir("/sys/class/net");
	if (!d)
		return UBUS_STATUS_UNKNOWN_ERROR;

	while ((e = readdir(d)) != NULL) {
		if (e->d_name[0] == '.')
			continue;

		if (pse_get(unl, e->d_name, &info) || !pse_present(&info))
			continue;

		cb(e->d_name, &info, priv);
	}

	closedir(d);

	return UBUS_STATUS_OK;
}

static void
info_port_cb(const char *dev, const struct pse_info *info, void *priv)
{
	uint64_t *total = priv;

	*total += add_port_compat(dev, info);
}

static int
rpc_poe_info(struct ubus_context *ctx, struct ubus_object *obj,
	     struct ubus_request_data *req, const char *method,
	     struct blob_attr *msg)
{
	uint64_t total = 0;
	struct unl unl;
	void *c;
	int rv;

	rv = pse_open(&unl);
	if (rv)
		return rv;

	blob_buf_init(&buf, 0);

	c = blobmsg_open_table(&buf, "ports");
	rv = for_each_pse_port(&unl, info_port_cb, &total);
	blobmsg_close_table(&buf, c);

	blobmsg_add_double(&buf, "consumption", total / 1000.0);

	unl_free(&unl);

	if (rv)
		return rv;

	ubus_send_reply(ctx, req, buf.head);

	return UBUS_STATUS_OK;
}

static void
status_port_cb(const char *dev, const struct pse_info *info, void *priv)
{
	add_port_status(dev, info);
}

static int
rpc_poe_status(struct ubus_context *ctx, struct ubus_object *obj,
	       struct ubus_request_data *req, const char *method,
	       struct blob_attr *msg)
{
	struct blob_attr *tb[__RPC_P_MAX];
	struct pse_info info;
	struct unl unl;
	const char *dev;
	int rv;

	blobmsg_parse(rpc_poe_policy, __RPC_P_MAX, tb,
		      blob_data(msg), blob_len(msg));

	rv = pse_open(&unl);
	if (rv)
		return rv;

	blob_buf_init(&buf, 0);

	if (tb[RPC_P_PORT]) {
		dev = blobmsg_get_string(tb[RPC_P_PORT]);

		if (!port_valid(dev)) {
			rv = UBUS_STATUS_NOT_FOUND;
			goto out;
		}

		rv = errno_to_ubus(pse_get(&unl, dev, &info));
		if (rv)
			goto out;

		if (!pse_present(&info)) {
			rv = UBUS_STATUS_NOT_SUPPORTED;
			goto out;
		}

		add_port_status(dev, &info);
	} else {
		rv = for_each_pse_port(&unl, status_port_cb, NULL);
		if (rv)
			goto out;
	}

	ubus_send_reply(ctx, req, buf.head);

out:
	unl_free(&unl);
	return rv;
}

static int
poe_apply(struct blob_attr **tb, bool require_enable)
{
	struct pse_info info;
	struct nl_msg *msg;
	struct unl unl;
	const char *dev;
	uint32_t state;
	int rv;

	if (!tb[RPC_P_PORT])
		return UBUS_STATUS_INVALID_ARGUMENT;

	if (require_enable && !tb[RPC_P_ENABLE])
		return UBUS_STATUS_INVALID_ARGUMENT;

	if (!tb[RPC_P_ENABLE] && !tb[RPC_P_PRIORITY] && !tb[RPC_P_POWER_LIMIT])
		return UBUS_STATUS_INVALID_ARGUMENT;

	if ((tb[RPC_P_PRIORITY] && (int32_t)blobmsg_get_u32(tb[RPC_P_PRIORITY]) < 0) ||
	    (tb[RPC_P_POWER_LIMIT] && (int32_t)blobmsg_get_u32(tb[RPC_P_POWER_LIMIT]) < 0))
		return UBUS_STATUS_INVALID_ARGUMENT;

	dev = blobmsg_get_string(tb[RPC_P_PORT]);
	if (!port_valid(dev))
		return UBUS_STATUS_NOT_FOUND;

	rv = pse_open(&unl);
	if (rv)
		return rv;

	/* find out whether this is a C33 or a PoDL PSE */
	rv = errno_to_ubus(pse_get(&unl, dev, &info));
	if (rv)
		goto out;

	if (!pse_present(&info)) {
		rv = UBUS_STATUS_NOT_SUPPORTED;
		goto out;
	}

	msg = pse_msg(&unl, ETHTOOL_MSG_PSE_SET, dev);
	if (!msg) {
		rv = UBUS_STATUS_UNKNOWN_ERROR;
		goto out;
	}

	if (tb[RPC_P_ENABLE]) {
		if (pse_has(&info, ETHTOOL_A_C33_PSE_ADMIN_STATE)) {
			state = blobmsg_get_bool(tb[RPC_P_ENABLE])
				? ETHTOOL_C33_PSE_ADMIN_STATE_ENABLED
				: ETHTOOL_C33_PSE_ADMIN_STATE_DISABLED;
			nla_put_u32(msg, ETHTOOL_A_C33_PSE_ADMIN_CONTROL, state);
		} else {
			state = blobmsg_get_bool(tb[RPC_P_ENABLE])
				? ETHTOOL_PODL_PSE_ADMIN_STATE_ENABLED
				: ETHTOOL_PODL_PSE_ADMIN_STATE_DISABLED;
			nla_put_u32(msg, ETHTOOL_A_PODL_PSE_ADMIN_CONTROL, state);
		}
	}

	if (tb[RPC_P_PRIORITY])
		nla_put_u32(msg, ETHTOOL_A_PSE_PRIO,
			    blobmsg_get_u32(tb[RPC_P_PRIORITY]));

	if (tb[RPC_P_POWER_LIMIT])
		nla_put_u32(msg, ETHTOOL_A_C33_PSE_AVAIL_PW_LIMIT,
			    blobmsg_get_u32(tb[RPC_P_POWER_LIMIT]));

	rv = errno_to_ubus(unl_request(&unl, msg, NULL, NULL));

out:
	unl_free(&unl);
	return rv;
}

static int
rpc_poe_manage(struct ubus_context *ctx, struct ubus_object *obj,
	       struct ubus_request_data *req, const char *method,
	       struct blob_attr *msg)
{
	struct blob_attr *tb[__RPC_P_MAX];

	blobmsg_parse(rpc_poe_policy, __RPC_P_MAX, tb,
		      blob_data(msg), blob_len(msg));

	/* manage only toggles power, like realtek-poe */
	tb[RPC_P_PRIORITY] = NULL;
	tb[RPC_P_POWER_LIMIT] = NULL;

	return poe_apply(tb, true);
}

static int
rpc_poe_set(struct ubus_context *ctx, struct ubus_object *obj,
	    struct ubus_request_data *req, const char *method,
	    struct blob_attr *msg)
{
	struct blob_attr *tb[__RPC_P_MAX];

	blobmsg_parse(rpc_poe_policy, __RPC_P_MAX, tb,
		      blob_data(msg), blob_len(msg));

	return poe_apply(tb, false);
}

static int
rpc_poe_reload(struct ubus_context *ctx, struct ubus_object *obj,
	       struct ubus_request_data *req, const char *method,
	       struct blob_attr *msg)
{
	/* nothing cached, state always comes from the kernel */
	return UBUS_STATUS_OK;
}

static int
rpc_poe_api_init(const struct rpc_daemon_ops *o, struct ubus_context *ctx)
{
	static const struct ubus_method poe_methods[] = {
		UBUS_METHOD_NOARG("info",   rpc_poe_info),
		UBUS_METHOD_MASK("status",  rpc_poe_status, rpc_poe_policy,
				 1 << RPC_P_PORT),
		UBUS_METHOD_MASK("manage",  rpc_poe_manage, rpc_poe_policy,
				 (1 << RPC_P_PORT) | (1 << RPC_P_ENABLE)),
		UBUS_METHOD("set",          rpc_poe_set,    rpc_poe_policy),
		UBUS_METHOD_NOARG("reload", rpc_poe_reload),
	};

	static struct ubus_object_type poe_type =
		UBUS_OBJECT_TYPE("rpcd-plugin-ethtool-poe", poe_methods);

	static struct ubus_object obj = {
		.name = "poe",
		.type = &poe_type,
		.methods = poe_methods,
		.n_methods = ARRAY_SIZE(poe_methods),
	};

	return ubus_add_object(ctx, &obj);
}

struct rpc_plugin rpc_plugin = {
	.init = rpc_poe_api_init
};
