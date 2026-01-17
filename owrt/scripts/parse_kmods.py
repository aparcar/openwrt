#!/usr/bin/env python3
"""
Parse OpenWrt kernel module definitions from .mk files and convert to YAML.

This script reads the module definition files from:
  package/kernel/linux/modules/*.mk

And generates a consolidated kmods.yaml file with all kmod definitions.
"""

import re
import sys
from pathlib import Path
from typing import Dict, List, Any, Optional
import yaml


def extract_menu_variables(content: str) -> Dict[str, str]:
    """Extract MENU variable definitions from content."""
    menus = {}
    # Match patterns like: BLOCK_MENU:=Block Devices, MENU_TITLE:=Something, W1_MENU:=W1 support
    # Capture any variable that contains MENU in its name
    pattern = r'^([A-Z_]*MENU[A-Z_]*|MENU_[A-Z_]+)\s*:?=\s*(.+)$'
    for match in re.finditer(pattern, content, re.MULTILINE):
        var_name = match.group(1).strip()
        var_value = match.group(2).strip()
        menus[var_name] = var_value
    return menus


def parse_makefile_define(content: str, name: str) -> Optional[str]:
    """Extract content of a define block."""
    pattern = rf'define\s+{re.escape(name)}\s*\n(.*?)\nendef'
    match = re.search(pattern, content, re.DOTALL)
    if match:
        return match.group(1).strip()
    return None


def parse_kconfig(kconfig_str: str) -> List[Dict[str, Any]]:
    """Parse KCONFIG string into list of config options."""
    if not kconfig_str:
        return []

    configs = []
    # Split by whitespace or backslash-newline continuations
    kconfig_str = kconfig_str.replace('\\\n', ' ')

    for item in kconfig_str.split():
        item = item.strip()
        # Skip empty, comments, variables, and lone backslash continuation chars
        if not item or item.startswith('#') or item.startswith('$') or item == '\\':
            continue

        # Handle CONFIG_X=y or just CONFIG_X
        if '=' in item:
            name, value = item.split('=', 1)
            # Skip if name is empty or just backslash
            if name.strip() and name.strip() != '\\':
                configs.append({'name': name, 'value': value})
        else:
            # Only add if it looks like a config option (starts with CONFIG_)
            if item.startswith('CONFIG_'):
                configs.append({'name': item, 'value': 'm'})  # Default to module

    return configs


def parse_files(files_str: str) -> List[str]:
    """Parse FILES string into list of module paths."""
    if not files_str:
        return []

    files = []
    # Replace Make variables with placeholders
    files_str = files_str.replace('$(LINUX_DIR)/', '')
    files_str = files_str.replace('$(PKG_BUILD_DIR)/', '')
    files_str = files_str.replace('\\\n', ' ')

    for item in files_str.split():
        item = item.strip()
        if not item or item.startswith('$') or item.startswith('#'):
            continue
        files.append(item)

    return files


def parse_autoload(autoload_str: str) -> Dict[str, Any]:
    """Parse AUTOLOAD string into structured format."""
    result = {'priority': 50, 'modules': [], 'boot': False}

    if not autoload_str:
        return result

    # Handle $(call AutoLoad,priority,modules,boot)
    autoload_match = re.search(r'AutoLoad,(\d+),([^,\)]+)(?:,(\d+))?', autoload_str)
    if autoload_match:
        result['priority'] = int(autoload_match.group(1))
        modules = autoload_match.group(2).strip()
        result['modules'] = [m.strip() for m in modules.split() if m.strip()]
        if autoload_match.group(3):
            result['boot'] = autoload_match.group(3) == '1'
        return result

    # Handle $(call AutoProbe,modules,boot)
    probe_match = re.search(r'AutoProbe,([^,\)]+)(?:,(\d+))?', autoload_str)
    if probe_match:
        result['priority'] = 0
        modules = probe_match.group(1).strip()
        result['modules'] = [m.strip() for m in modules.split() if m.strip()]
        if probe_match.group(2):
            result['boot'] = probe_match.group(2) == '1'
        return result

    return result


def parse_depends(depends_str: str) -> List[str]:
    """Parse DEPENDS string into list of dependencies."""
    if not depends_str:
        return []

    deps = []
    depends_str = depends_str.replace('\\\n', ' ')

    for item in depends_str.split():
        item = item.strip()
        if not item:
            continue
        # Handle +kmod-xxx and @TARGET_xxx style dependencies
        if item.startswith('+'):
            deps.append(item[1:])
        elif item.startswith('@'):
            # Target/feature dependency - keep as-is
            deps.append(item)
        elif item.startswith('PACKAGE_'):
            continue  # Skip conditional package dependencies
        else:
            deps.append(item)

    return deps


def extract_adddepends_categories(content: str, menus: Dict[str, str]) -> Dict[str, str]:
    """Extract category mappings from AddDepends/xxx macros."""
    category_map = {}

    # Find all AddDepends/xxx definitions that set SUBMENU
    pattern = r'define\s+AddDepends/([a-zA-Z0-9_-]+)\s*\n(.*?)\nendef'
    for match in re.finditer(pattern, content, re.DOTALL):
        dep_name = match.group(1)
        dep_content = match.group(2)

        # Look for SUBMENU in the macro
        submenu_match = re.search(r'SUBMENU\s*:?=\s*\$\(([A-Z_]+_MENU)\)', dep_content)
        if submenu_match:
            menu_var = submenu_match.group(1)
            if menu_var in menus:
                category_map[dep_name] = menus[menu_var]
            else:
                category_map[dep_name] = menu_var.replace('_MENU', '').lower().replace('_', '-')

    return category_map


def parse_kmod_definition(content: str, name: str, menus: Dict[str, str],
                          adddepends_cats: Dict[str, str]) -> Optional[Dict[str, Any]]:
    """Parse a single KernelPackage definition."""
    define_content = parse_makefile_define(content, f'KernelPackage/{name}')
    if not define_content:
        return None

    kmod = {
        'name': name,
        'title': '',
        'description': '',
        'category': '',
        'kconfig': [],
        'files': [],
        'autoload': {'priority': 50, 'modules': [], 'boot': False},
        'depends': [],
        'hidden': False,
    }

    # Join lines that end with backslash (line continuations)
    lines = define_content.split('\n')
    joined_lines = []
    current_line = ''
    for line in lines:
        stripped = line.rstrip()
        if stripped.endswith('\\'):
            # Line continues - add without backslash
            current_line += stripped[:-1] + ' '
        else:
            current_line += line
            joined_lines.append(current_line)
            current_line = ''
    if current_line:
        joined_lines.append(current_line)

    # Parse key-value pairs
    for line in joined_lines:
        line = line.strip()
        if not line or line.startswith('#'):
            continue

        if ':=' in line:
            key, _, value = line.partition(':=')
            key = key.strip()
            value = value.strip()

            if key == 'TITLE':
                kmod['title'] = value
            elif key == 'SUBMENU':
                # Extract category from $(XXX_MENU) or literal
                menu_match = re.search(r'\$\(([A-Z_]+_MENU)\)', value)
                if menu_match:
                    menu_var = menu_match.group(1)
                    if menu_var in menus:
                        kmod['category'] = menus[menu_var]
                    else:
                        kmod['category'] = menu_var.replace('_MENU', '').lower().replace('_', '-')
                else:
                    kmod['category'] = value
            elif key == 'KCONFIG':
                kmod['kconfig'] = parse_kconfig(value)
            elif key == 'FILES':
                kmod['files'] = parse_files(value)
            elif key == 'AUTOLOAD':
                kmod['autoload'] = parse_autoload(value)
            elif key == 'DEPENDS':
                kmod['depends'] = parse_depends(value)
            elif key == 'HIDDEN':
                kmod['hidden'] = value == '1'

        # Check for $(call AddDepends/xxx) to get category
        adddep_match = re.search(r'\$\(call\s+AddDepends/([a-zA-Z0-9_-]+)', line)
        if adddep_match and not kmod['category']:
            dep_name = adddep_match.group(1)
            if dep_name in adddepends_cats:
                kmod['category'] = adddepends_cats[dep_name]

    # Get description if separate define exists
    desc_content = parse_makefile_define(content, f'KernelPackage/{name}/description')
    if desc_content:
        kmod['description'] = desc_content.strip()

    return kmod


def find_kmod_names(content: str) -> List[str]:
    """Find all KernelPackage names defined in file."""
    # Find $(eval $(call KernelPackage,xxx))
    pattern = r'\$\(eval\s+\$\(call\s+KernelPackage,([a-zA-Z0-9_-]+)\)\)'
    return re.findall(pattern, content)


def parse_mk_file(mk_path: Path, global_menus: Dict[str, str] = None,
                  global_adddeps: Dict[str, str] = None) -> tuple:
    """Parse all kmod definitions from a .mk file."""
    content = mk_path.read_text()

    # Extract menu variables from this file
    menus = {}
    if global_menus:
        menus.update(global_menus)
    menus.update(extract_menu_variables(content))

    # Extract AddDepends category mappings
    adddeps = {}
    if global_adddeps:
        adddeps.update(global_adddeps)
    adddeps.update(extract_adddepends_categories(content, menus))

    # Default category from filename (e.g., crypto.mk -> Cryptographic modules)
    default_category = mk_path.stem.replace('-', ' ').title() + ' modules'
    if default_category == '001 Depends modules':
        default_category = ''

    kmods = []
    names = find_kmod_names(content)
    for name in names:
        kmod = parse_kmod_definition(content, name, menus, adddeps)
        if kmod:
            # Add source file for reference
            kmod['source_file'] = mk_path.name
            # Set default category if not found
            if not kmod['category']:
                kmod['category'] = default_category
            kmods.append(kmod)

    return kmods, menus, adddeps


def main():
    # Find OpenWrt root
    script_dir = Path(__file__).parent
    poc_dir = script_dir.parent
    openwrt_dir = poc_dir.parent

    modules_dir = openwrt_dir / 'package' / 'kernel' / 'linux' / 'modules'

    if not modules_dir.exists():
        print(f"Error: Modules directory not found: {modules_dir}")
        sys.exit(1)

    print(f"Parsing kmod definitions from: {modules_dir}")

    all_kmods = []
    all_menus = {}
    all_adddeps = {}

    # Parse all .mk files (001-depends.mk first for global menus)
    mk_files = sorted(modules_dir.glob('*.mk'))
    for mk_file in mk_files:
        kmods, menus, adddeps = parse_mk_file(mk_file, all_menus, all_adddeps)
        all_menus.update(menus)
        all_adddeps.update(adddeps)
        print(f"  {mk_file.name}: {len(kmods)} kmods")
        all_kmods.extend(kmods)

    print(f"\nTotal kmods parsed: {len(all_kmods)}")
    print(f"AddDepends categories found: {len(all_adddeps)}")

    # Organize by category
    by_category = {}
    for kmod in all_kmods:
        cat = kmod.get('category', 'Other')
        # Clean up category name - remove any remaining variable references
        if cat.startswith('$(') and cat.endswith(')'):
            # Try to resolve from menus
            var_name = cat[2:-1]
            cat = all_menus.get(var_name, var_name.replace('_', ' ').title())
        if cat not in by_category:
            by_category[cat] = []
        by_category[cat].append(kmod)

    # Generate YAML output - organized by category
    categories_list = []
    for cat_name in sorted(by_category.keys()):
        mods = by_category[cat_name]
        # Build module list for this category (without redundant category field)
        module_list = []
        for m in sorted(mods, key=lambda x: x['name']):
            mod_entry = {
                'name': m['name'],
                'title': m.get('title', ''),
                'description': m.get('description', ''),
                'kconfig': m.get('kconfig', []),
                'files': m.get('files', []),
                'autoload': m.get('autoload', {}),
                'depends': m.get('depends', []),
            }
            # Only include optional fields if they have values
            if m.get('hidden'):
                mod_entry['hidden'] = True
            module_list.append(mod_entry)

        categories_list.append({
            'name': cat_name,
            'modules': module_list,
        })

    output = {
        'version': '1.0',
        'description': 'OpenWrt kernel module definitions (auto-generated)',
        'categories': categories_list,
    }

    # Write to kmods.yaml
    output_path = poc_dir / 'kmods.yaml'
    with open(output_path, 'w') as f:
        yaml.dump(output, f, default_flow_style=False, sort_keys=False, width=120)

    print(f"\nOutput written to: {output_path}")

    # Print category summary
    print("\nCategories:")
    for cat in output['categories']:
        print(f"  {cat['name']}: {len(cat['modules'])} modules")


if __name__ == '__main__':
    main()
