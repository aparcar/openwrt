# SPDX-License-Identifier: GPL-2.0-only
#
# Copyright (C) 2007-2020 OpenWrt.org

# define a dependency on a subtree
# parameters:
#	1: directories/files
#	2: directory dependency
#	3: tempfile for file listings
#	4: find options

DEP_FINDPARAMS := -x "*/.svn*" -x "*/.*" -x "*:*" -x "*\!*" -x "* *" -x "*\\\#*" -x "*/.pkgdir*"

# Content-based hash (hashes file contents, not timestamps) - now the default
# This prevents unnecessary rebuilds when files are touched but content unchanged
find_md5=find $(wildcard $(1)) -type f $(patsubst -x,-and -not -path,$(DEP_FINDPARAMS) $(2)) -print0 | xargs -0 $(MKHASH) md5 | sort | $(MKHASH) md5
# Alias for backwards compatibility
find_md5_reproducible=$(call find_md5,$(1),$(2))

define rdep
  .PRECIOUS: $(2)
  .SILENT: $(2)_check

  $(2): $(2)_check
  check-depends: $(2)_check

  $(2)_check::
	@current_hash=$$$$($(call find_md5,$(1),$(4))); \
	if [ -f "$(2)" ]; then \
		stored_hash=$$$$(cat "$(2).hash" 2>/dev/null || echo ""); \
		if [ "$$$$current_hash" = "$$$$stored_hash" ]; then \
			$(call debug_eval,$(SUBDIR),r,echo "No need to rebuild $(2) (hash unchanged)";) \
			touch "$(2)_check"; \
			touch -r "$(2)" "$(2)_check"; \
		else \
			$(call debug_eval,$(SUBDIR),r,echo "Need to rebuild $(2) (hash changed)";) \
			touch "$(2)_check"; \
		fi; \
	else \
		$(call debug_eval,$(SUBDIR),r,echo "Target $(2) not built";) \
		mkdir -p "$$$$(dirname "$(2)")"; \
		touch "$(2)_check"; \
	fi; \
	echo "$$$$current_hash" > "$(2).hash"

endef

ifeq ($(filter .%,$(MAKECMDGOALS)),$(if $(MAKECMDGOALS),$(MAKECMDGOALS),x))
  define rdep
    $(2): $(2)_check
  endef
endif
