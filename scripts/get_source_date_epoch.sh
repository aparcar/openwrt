#!/usr/bin/env bash
export LANG=C
export LC_ALL=C

if [ -n "$TOPDIR" ]; then
	cd "$TOPDIR" || exit 1
fi

SOURCE="${1:-.}"

try_version() {
	[ -f "$SOURCE/version.date" ] || return 1
	SOURCE_DATE_EPOCH=$(cat "$SOURCE/version.date")
	[ -n "$SOURCE_DATE_EPOCH" ]
}

try_git() {
	SOURCE_DATE_EPOCH=$(git -C "$SOURCE" log -1 --no-show-signature \
		--format=format:%ct "$SOURCE" 2>/dev/null)
	[ -n "$SOURCE_DATE_EPOCH" ]
}

try_hg() {
	SOURCE_DATE_EPOCH=$(hg --cwd "$SOURCE" log --template '{date}' -l 1 \
		"$SOURCE" 2>/dev/null | cut -d. -f1)
	[ -n "$SOURCE_DATE_EPOCH" ]
}

try_mtime() {
	# Fall back to the most recent modification time of the source contents.
	# Unlike the mtime of this script, the contents' mtimes are restored from
	# the (reproducibly packed) source tarball, so the result stays static
	# regardless of how OpenWrt / the feeds were checked out.
	SOURCE_DATE_EPOCH=$(find "$SOURCE" -type f -printf '%T@\n' 2>/dev/null \
		| sort -rn | head -n1 | cut -d. -f1)
	[ -n "$SOURCE_DATE_EPOCH" ]
}

try_version || try_git || try_hg || try_mtime || SOURCE_DATE_EPOCH=""
echo "$SOURCE_DATE_EPOCH"
