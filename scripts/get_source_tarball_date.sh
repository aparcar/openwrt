#!/usr/bin/env bash
#
# Print the modification time (UTC epoch) of the first entry of a source
# tarball.
#
# OpenWrt repacks sources fetched from git (and several other VCS) into
# reproducible tarballs in which every entry carries the upstream commit
# date.  Plain upstream release archives carry the dates set by their
# author.  In both cases the timestamp stored inside the tarball is a
# property of the remote source and therefore static, regardless of how
# OpenWrt / the feeds themselves were checked out (git, release tarball,
# SDK snapshot, ...).
#
# Deriving SOURCE_DATE_EPOCH from this value keeps reproducible package
# builds independent of the local checkout method.

export LANG=C
export LC_ALL=C

tarball="$1"
[ -f "$tarball" ] || exit 1

case "$tarball" in
	*.tar.gz|*.tgz)         decompress="gzip -dc";;
	*.tar.bz2|*.tbz2|*.tbz) decompress="bzip2 -dc";;
	*.tar.xz|*.txz)         decompress="xz -dc";;
	*.tar.zst)              decompress="zstd -dc";;
	*.tar)                  decompress="cat";;
	*)                      exit 1;;
esac

# List the archive with full timestamps interpreted as UTC and grab the
# date/time of the first entry.  The reader exits after the first line, so
# the decompressor only has to produce the very first tar header.
date=$($decompress "$tarball" 2>/dev/null | \
	TZ=UTC0 "${TAR:-tar}" --full-time -tvf - 2>/dev/null | \
	awk 'NF>=5 { print $4" "$5; exit }')

[ -n "$date" ] || exit 1

date -d "$date UTC" +%s 2>/dev/null
