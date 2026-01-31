# Cloud-Init Support for OpenWrt - Implementation Plan

## Overview

Add cloud-init support to OpenWrt for automated instance configuration in cloud/VM environments.

## Repository Structure

All new packages go into **`aparcar/packages`** feed:

```
aparcar/packages/
├── utils/ucode-mod-yaml/    # NEW: ucode YAML parser module
└── utils/cloud-init/        # NEW: cloud-init implementation
```

Existing dependency in packages feed:
- `libs/yaml/` - libyaml (already exists)

---

## Package 1: ucode-mod-yaml

### Purpose
Provide YAML parsing capabilities to ucode scripts by wrapping libyaml.

### Structure
```
utils/ucode-mod-yaml/
├── Makefile
└── src/
    └── yaml.c
```

### Makefile
```makefile
include $(TOPDIR)/rules.mk

PKG_NAME:=ucode-mod-yaml
PKG_RELEASE:=1
PKG_LICENSE:=ISC
PKG_MAINTAINER:=OpenWrt Developers

include $(INCLUDE_DIR)/package.mk

define Package/ucode-mod-yaml
  SECTION:=utils
  CATEGORY:=Utilities
  TITLE:=ucode YAML parser module
  DEPENDS:=+libucode +libyaml
endef

define Package/ucode-mod-yaml/description
  The yaml module provides YAML parsing and emitting capabilities
  for ucode scripts using libyaml.
endef

define Build/Compile
	$(TARGET_CC) $(TARGET_CFLAGS) $(TARGET_LDFLAGS) $(FPIC) \
		-Wall -ffunction-sections -Wl,--gc-sections -shared \
		-lyaml \
		-o $(PKG_BUILD_DIR)/yaml.so $(PKG_BUILD_DIR)/yaml.c
endef

define Package/ucode-mod-yaml/install
	$(INSTALL_DIR) $(1)/usr/lib/ucode
	$(CP) $(PKG_BUILD_DIR)/yaml.so $(1)/usr/lib/ucode/
endef

$(eval $(call BuildPackage,ucode-mod-yaml))
```

### C Implementation (yaml.c)

Core functions to implement:

```c
#include <ucode/module.h>
#include <yaml.h>

// yaml.parse(string) -> object/array
// Parse YAML string into ucode data structure
static uc_value_t *
uc_yaml_parse(uc_vm_t *vm, size_t nargs);

// yaml.stringify(value) -> string
// Convert ucode value to YAML string
static uc_value_t *
uc_yaml_stringify(uc_vm_t *vm, size_t nargs);

static const uc_function_list_t global_fns[] = {
    { "parse",     uc_yaml_parse },
    { "stringify", uc_yaml_stringify },
};

void uc_module_init(uc_vm_t *vm, uc_value_t *scope)
{
    uc_function_list_register(scope, global_fns);
}
```

### Usage in ucode
```javascript
import * as yaml from 'yaml';

let config = yaml.parse(`
#cloud-config
hostname: router1
ssh_authorized_keys:
  - ssh-ed25519 AAAA... user@host
`);

print(config.hostname);  // "router1"
```

### Size Estimate
- libyaml: ~60-80KB (shared library)
- ucode-mod-yaml: ~5-10KB
- Total: ~70-90KB

---

## Package 2: cloud-init

### Purpose
Lightweight cloud-init implementation for OpenWrt that reads configuration from standard cloud-init data sources and applies it via UCI.

### Supported Data Sources (by priority)

| Source | Priority | Detection |
|--------|----------|-----------|
| NoCloud | 100 | CD-ROM/USB with label "cidata" or "CIDATA" |
| ConfigDrive | 90 | Partition with label "config-2" |
| EC2 IMDS | 80 | HTTP 169.254.169.254 reachable |

### Supported cloud-config Directives

| Directive | Description | Implementation |
|-----------|-------------|----------------|
| `hostname` | Set system hostname | `uci set system.@system[0].hostname` |
| `ssh_authorized_keys` | Inject SSH public keys | Write to `/etc/dropbear/authorized_keys` |
| `password` | Set root password | Modify `/etc/shadow` |
| `chpasswd` | Change passwords | Modify `/etc/shadow` |
| `write_files` | Create/modify files | Direct file writes |
| `runcmd` | Execute commands | Shell execution |
| `packages` | Install packages | `opkg install` |

### Package Structure
```
utils/cloud-init/
├── Makefile
└── files/
    ├── etc/
    │   ├── config/
    │   │   └── cloud-init           # UCI config
    │   ├── init.d/
    │   │   └── cloud-init           # Procd init script
    │   ├── uci-defaults/
    │   │   └── 05-cloud-init        # First-boot integration
    │   └── hotplug.d/
    │       └── block/
    │           └── 10-cloud-init    # Block device detection
    └── usr/
        ├── sbin/
        │   └── cloud-init           # Main executable (ucode)
        └── lib/
            └── cloud-init/
                ├── datasources.uc   # Data source detection
                ├── modules/
                │   ├── hostname.uc
                │   ├── ssh_keys.uc
                │   ├── password.uc
                │   ├── write_files.uc
                │   └── runcmd.uc
                └── utils.uc
```

### Makefile
```makefile
include $(TOPDIR)/rules.mk

PKG_NAME:=cloud-init
PKG_RELEASE:=1
PKG_LICENSE:=GPL-2.0
PKG_MAINTAINER:=OpenWrt Developers

include $(INCLUDE_DIR)/package.mk

define Package/cloud-init
  SECTION:=utils
  CATEGORY:=Utilities
  TITLE:=Cloud-init support for OpenWrt
  DEPENDS:=+ucode +ucode-mod-fs +ucode-mod-uci +ucode-mod-ubus \
           +ucode-mod-yaml +blkid +block-mount +uclient-fetch
  PKGARCH:=all
endef

define Package/cloud-init/description
  Lightweight cloud-init implementation for OpenWrt.
  Supports NoCloud, ConfigDrive, and EC2 metadata sources
  for automated instance configuration.
endef

define Package/cloud-init/conffiles
/etc/config/cloud-init
endef

define Build/Compile
endef

define Package/cloud-init/install
	$(CP) ./files/* $(1)/
endef

$(eval $(call BuildPackage,cloud-init))
```

### Optional kernel modules (for NoCloud CD-ROM/USB)
```
+kmod-fs-iso9660   # For CD-ROM data sources
+kmod-fs-vfat      # For USB FAT data sources
+kmod-usb-storage  # For USB detection
```

---

## Boot Integration

### Execution Flow

```
Boot
  │
  ├─► /etc/init.d/boot (START=10)
  │     └─► uci_apply_defaults()
  │           └─► /etc/uci-defaults/05-cloud-init
  │                 └─► /usr/sbin/cloud-init --first-boot
  │                       ├─► Detect data source
  │                       ├─► Mount & read user-data/meta-data
  │                       ├─► Parse YAML (via ucode-mod-yaml)
  │                       ├─► Apply configuration modules
  │                       ├─► Mark as done (/etc/cloud-init.done)
  │                       └─► Exit
  │
  └─► /etc/init.d/network (START=20)
        └─► (with cloud-init applied config)
```

### Hotplug Detection

For late-arriving media (USB inserted after boot):
```
/etc/hotplug.d/block/10-cloud-init
  └─► Check for cidata/config-2 label
        └─► Run cloud-init if not already done
```

---

## UCI Configuration

`/etc/config/cloud-init`:
```
config cloud-init 'config'
    option enabled '1'
    option datasources 'nocloud configdrive ec2'

config datasource 'nocloud'
    option enabled '1'
    option seed_dir '/var/lib/cloud/seed/nocloud'

config datasource 'configdrive'
    option enabled '1'

config datasource 'ec2'
    option enabled '1'
    option metadata_url 'http://169.254.169.254'
```

---

## Security Considerations

1. **Read-only mounts** - Data sources mounted read-only
2. **Label validation** - Only mount media with expected labels
3. **Credential security** - 0600 permissions on keys/passwords
4. **One-time execution** - Marked done after first run
5. **Logging** - All actions logged via `logger -t cloud-init`

---

## Example cloud-config

```yaml
#cloud-config
hostname: openwrt-router

ssh_authorized_keys:
  - ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAA... admin@example.com

password: $6$rounds=4096$salt$hash...

write_files:
  - path: /etc/banner
    content: |
      Welcome to OpenWrt Cloud Instance
    permissions: '0644'

runcmd:
  - opkg update
  - opkg install luci
```

---

## Implementation Order

1. **Phase 1: ucode-mod-yaml**
   - Implement yaml.parse()
   - Implement yaml.stringify()
   - Test with cloud-config samples

2. **Phase 2: cloud-init core**
   - Data source detection (NoCloud first)
   - Basic module framework
   - UCI defaults integration

3. **Phase 3: Configuration modules**
   - hostname
   - ssh_authorized_keys
   - password/chpasswd
   - write_files
   - runcmd

4. **Phase 4: Additional data sources**
   - ConfigDrive
   - EC2 IMDS

---

## Size Estimate

| Component | Size |
|-----------|------|
| libyaml (dependency) | ~70KB |
| ucode-mod-yaml | ~8KB |
| cloud-init (ucode scripts) | ~15KB |
| **Total** | **~93KB** |

Plus optional kernel modules for filesystem support if not already included.
