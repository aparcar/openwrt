#!/bin/sh
# SPDX-License-Identifier: GPL-2.0-only
#
# Attach uci-defaults provisioning scripts to an OpenWrt sysupgrade image.
#
# Usage: provision-image.sh <image> <scripts-dir>
#
# Every regular file in <scripts-dir> is stored verbatim in the image's fwtool
# metadata under provisioning."uci-defaults".<filename>. On the next sysupgrade
# the device extracts the scripts to /etc/uci-defaults/ and runs them once on
# first boot. See package/base-files/files/lib/upgrade/fwtool.sh for the
# receiving side.
#
# The image is modified in place. Any fwtool signature is dropped, so the image
# has to be re-signed afterwards (e.g. using scripts/sign_images.sh) if desired.
#
# This reimplements the fwtool metadata trailer instead of depending on the
# fwtool binary, so it also runs on a plain build host. The CRC32 required by
# the trailer is obtained from gzip's own trailer to avoid extra dependencies.

set -e

FWIMAGE_MAGIC="46577830" # "FWx0"
FWIMAGE_INFO=1

usage() {
	echo "Usage: $0 <image> <scripts-dir>" >&2
	exit 1
}

image="$1"
dir="$2"

[ -n "$image" ] && [ -n "$dir" ] || usage
[ -f "$image" ] || { echo "Image '$image' not found" >&2; exit 1; }
[ -d "$dir" ] || { echo "Scripts directory '$dir' not found" >&2; exit 1; }
command -v jq >/dev/null 2>&1 || { echo "jq is required" >&2; exit 1; }

cleanup() { rm -f "$meta_file" "$out"; }
meta_file="$(mktemp)"; out="$(mktemp)"
trap cleanup EXIT

# read $2 bytes from the image starting at 0-based byte offset $1
read_range() {
	tail -c "+$(($1 + 1))" "$image" | head -c "$2"
}

# convert an even-length hex string on stdin to raw bytes on stdout
hex_to_bin() {
	local h="$1" byte
	while [ -n "$h" ]; do
		byte="$(printf '%s' "$h" | cut -c1-2)"
		h="$(printf '%s' "$h" | cut -c3-)"
		printf '%b' "\\0$(printf '%03o' "0x$byte")"
	done
}

filesize="$(wc -c < "$image" | tr -d ' ')"

# Peel all trailing fwtool sections (metadata and signature) off the image to
# find the plain firmware boundary and the current metadata to merge into.
core_end="$filesize"
pos="$filesize"
while [ "$pos" -ge 16 ]; do
	trailer="$(read_range $((pos - 16)) 16 | od -An -tx1 | tr -d ' \n')"
	[ "$(printf '%s' "$trailer" | cut -c1-8)" = "$FWIMAGE_MAGIC" ] || break

	sec_type="$(printf '%d' "0x$(printf '%s' "$trailer" | cut -c17-18)")"
	sec_size="$(printf '%d' "0x$(printf '%s' "$trailer" | cut -c25-32)")"
	sec_start=$((pos - sec_size))

	# grab the newest (last) metadata section as merge base
	if [ "$sec_type" -eq "$FWIMAGE_INFO" ] && [ ! -s "$meta_file" ]; then
		read_range $((sec_start + 8)) $((pos - 16 - (sec_start + 8))) > "$meta_file"
	fi

	pos="$sec_start"
	core_end="$sec_start"
done

[ -s "$meta_file" ] || {
	echo "Image has no fwtool metadata; cannot attach provisioning" >&2
	exit 1
}

# Build the provisioning object from the scripts directory.
prov='{}'
count=0
for f in "$dir"/*; do
	[ -f "$f" ] || continue
	name="$(basename "$f")"
	prov="$(printf '%s' "$prov" | jq --arg n "$name" --rawfile v "$f" '. + {($n): $v}')"
	echo "  + $name"
	count=$((count + 1))
done
[ "$count" -gt 0 ] || { echo "No scripts found in '$dir'" >&2; exit 1; }

# Merge into the existing metadata, keeping any provisioning scripts already set.
new_json="$(jq -c --argjson prov "$prov" \
	'.provisioning."uci-defaults" = ((.provisioning."uci-defaults" // {}) + $prov)' \
	"$meta_file")"

# fwtool refuses metadata larger than METADATA_MAXLEN (30 KiB); fail loudly here
# instead of letting the device silently reject the image.
meta_bytes="$(printf '%s' "$new_json" | wc -c | tr -d ' ')"
if [ "$meta_bytes" -gt 30720 ]; then
	echo "Metadata is $meta_bytes bytes, exceeds fwtool's 30 KiB limit" >&2
	exit 1
fi

# Assemble the output: plain firmware, then the metadata section made of an
# 8-byte header, the metadata and a 16-byte trailer.
meta_len="$(printf '%s' "$new_json" | wc -c | tr -d ' ')"
total_size="$((8 + meta_len + 16))"
size_be="$(printf '%08x' "$total_size")"

# The CRC covers everything up to (but not including) the trailer, so build that
# range first: firmware core + header + metadata.
head -c "$core_end" "$image" > "$out"
head -c 8 /dev/zero >> "$out"
printf '%s' "$new_json" >> "$out"

# fwtool stores a running CRC32 over that whole range without the final
# inversion that zlib/gzip apply. Read the zlib CRC from gzip's trailer (it is
# computed over the uncompressed input, so the compression level is irrelevant)
# and invert it to match fwtool.
crc_le="$(gzip -1cn < "$out" | tail -c 8 | head -c 4 | od -An -tx1 | tr -d ' \n')"
crc_be_zlib="$(printf '%s' "$crc_le" | cut -c7-8)$(printf '%s' "$crc_le" | cut -c5-6)$(printf '%s' "$crc_le" | cut -c3-4)$(printf '%s' "$crc_le" | cut -c1-2)"
crc_be="$(printf '%08x' "$(( ~0x$crc_be_zlib & 0xffffffff ))")"

# magic + crc + type(1) + pad(3) + size, then replace the image
hex_to_bin "${FWIMAGE_MAGIC}${crc_be}01000000${size_be}" >> "$out"
cat "$out" > "$image"

echo "Attached $count provisioning script(s) to $image"
