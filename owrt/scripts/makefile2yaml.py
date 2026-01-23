#!/usr/bin/env python3
"""
Convert OpenWrt package Makefile to YAML format for the PoC build system.

Usage:
    python3 makefile2yaml.py /path/to/package/Makefile [output.yaml]

This script parses OpenWrt Makefile variables and generates a package.yaml
file compatible with the PoC build system.
"""

import re
import sys
import argparse
from pathlib import Path
from typing import Dict, List, Optional, Any


def parse_makefile(makefile_path: Path) -> Dict[str, Any]:
    """Parse an OpenWrt package Makefile and extract key variables."""
    content = makefile_path.read_text()
    
    # Dictionary to store parsed values
    pkg = {
        'name': '',
        'version': '',
        'release': '1',
        'license': '',
        'source': {},
        'metadata': {},
        'dependencies': {'runtime': [], 'build': []},
        'build': {},
        'subpackages': {},
    }
    
    # Helper to extract variable value
    def get_var(name: str, default: str = '') -> str:
        # Match PKG_NAME:=value or PKG_NAME=value
        patterns = [
            rf'^{name}\s*:?=\s*(.+?)$',
            rf'^{name}\s*\+=\s*(.+?)$',
        ]
        for pattern in patterns:
            match = re.search(pattern, content, re.MULTILINE)
            if match:
                value = match.group(1).strip()
                # Remove shell variable expansions like $(call ...)
                if value.startswith('$(') and ')' in value:
                    # Try to extract simple values
                    inner = re.search(r'\$\(call qstrip,\$\(([^)]+)\)\)', value)
                    if inner:
                        return get_var(inner.group(1), default)
                return value
        return default
    
    # Extract basic package info
    pkg['name'] = get_var('PKG_NAME')
    pkg['version'] = get_var('PKG_VERSION', get_var('PKG_SOURCE_DATE', '1.0.0'))
    pkg['release'] = get_var('PKG_RELEASE', '1')
    pkg['license'] = get_var('PKG_LICENSE', 'Unknown')
    
    # Source information
    source_proto = get_var('PKG_SOURCE_PROTO', 'tarball')
    source_url = get_var('PKG_SOURCE_URL')
    source_version = get_var('PKG_SOURCE_VERSION')
    source_hash = get_var('PKG_HASH', get_var('PKG_MIRROR_HASH'))
    source_date = get_var('PKG_SOURCE_DATE')
    
    if source_proto == 'git':
        pkg['source'] = {
            'type': 'git',
            'url': source_url.replace('$(PROJECT_GIT)', 'https://git.openwrt.org'),
            'version': source_version,
            'sha256': source_hash,
        }
        if source_date:
            pkg['version'] = source_date.replace('-', '.')
    else:
        source_file = get_var('PKG_SOURCE')
        if source_url:
            pkg['source'] = {
                'type': 'tarball',
                'url': source_url,
                'sha256': source_hash,
            }
    
    # Build dependencies
    build_depends = get_var('PKG_BUILD_DEPENDS')
    if build_depends:
        deps = [d.strip() for d in build_depends.split() if d.strip()]
        pkg['dependencies']['build'] = deps
    
    # Build system detection
    if 'include $(INCLUDE_DIR)/cmake.mk' in content:
        pkg['build']['system'] = 'cmake'
    elif 'include $(INCLUDE_DIR)/meson.mk' in content:
        pkg['build']['system'] = 'meson'
    elif 'include $(INCLUDE_DIR)/autotools.mk' in content:
        pkg['build']['system'] = 'autotools'
    elif 'Build/Compile' in content:
        pkg['build']['system'] = 'custom'
    else:
        pkg['build']['system'] = 'make'
    
    pkg['build']['parallel'] = 'PKG_BUILD_PARALLEL:=1' in content or 'PKG_BUILD_PARALLEL=1' in content
    
    # Extract configure arguments
    configure_args = []
    for match in re.finditer(r'CONFIGURE_ARGS\s*\+=?\s*(.+?)$', content, re.MULTILINE):
        args = match.group(1).strip()
        if not args.startswith('$('):
            configure_args.extend(args.split())
    if configure_args:
        pkg['build']['configure_args'] = configure_args
    
    # CMAKE options
    cmake_options = []
    for match in re.finditer(r'CMAKE_OPTIONS\s*\+=?\s*(.+?)$', content, re.MULTILINE):
        opt = match.group(1).strip()
        if not opt.startswith('$(if'):
            cmake_options.append(opt)
    if cmake_options:
        pkg['build']['configure_args'] = cmake_options
    
    # Parse Package definitions
    package_blocks = re.findall(
        r'define Package/([^\n]+)\n(.*?)endef',
        content,
        re.DOTALL
    )
    
    for pkg_name, block in package_blocks:
        pkg_name = pkg_name.strip()
        if '/' in pkg_name:  # Skip sub-definitions like Package/foo/description
            continue
        
        subpkg = {
            'description': '',
            'section': 'base',
            'files': [],
            'symlinks': [],
        }
        
        # Parse block content
        for line in block.strip().split('\n'):
            line = line.strip()
            if line.startswith('SECTION:='):
                subpkg['section'] = line.split(':=')[1].strip()
            elif line.startswith('CATEGORY:='):
                pkg['metadata']['category'] = line.split(':=')[1].strip()
            elif line.startswith('TITLE:='):
                subpkg['description'] = line.split(':=')[1].strip()
            elif line.startswith('DEPENDS:='):
                deps_str = line.split(':=')[1].strip()
                # Parse dependencies like +libubox +libuci
                deps = []
                for dep in deps_str.split():
                    dep = dep.strip()
                    if dep.startswith('+'):
                        dep = dep[1:]
                    # Remove conditionals like NAND_SUPPORT:ubi-utils
                    if ':' in dep and not dep.startswith('@'):
                        dep = dep.split(':')[1]
                    if dep and not dep.startswith('@'):
                        deps.append(dep)
                if deps:
                    subpkg['runtime_deps'] = deps
        
        # Try to find install section
        install_match = re.search(
            rf'define Package/{re.escape(pkg_name)}/install\n(.*?)endef',
            content,
            re.DOTALL
        )
        if install_match:
            install_block = install_match.group(1)
            
            # Parse INSTALL_BIN lines
            for match in re.finditer(
                r'\$\(INSTALL_BIN\)\s+\$\(PKG_INSTALL_DIR\)/([^\s]+)\s+\$\(1\)/([^\s]+)',
                install_block
            ):
                src, dst = match.groups()
                subpkg['files'].append({
                    'src': src.replace('$(PKG_NAME)', pkg['name']),
                    'dst': dst.replace('$(PKG_NAME)', pkg['name']),
                    'mode': '0755'
                })
            
            # Parse INSTALL_DATA lines
            for match in re.finditer(
                r'\$\(INSTALL_DATA\)\s+\$\(PKG_INSTALL_DIR\)/([^\s]+)\s+\$\(1\)/([^\s]+)',
                install_block
            ):
                src, dst = match.groups()
                subpkg['files'].append({
                    'src': src.replace('$(PKG_NAME)', pkg['name']),
                    'dst': dst.replace('$(PKG_NAME)', pkg['name']),
                    'mode': '0644'
                })
            
            # Parse LN (symlink) lines
            for match in re.finditer(
                r'\$\(LN\)\s+(\S+)\s+\$\(1\)/(\S+)',
                install_block
            ):
                src, dst = match.groups()
                subpkg['symlinks'].append({
                    'src': src,
                    'dst': dst
                })
        
        pkg['subpackages'][pkg_name] = subpkg
    
    # Set metadata
    pkg['metadata']['section'] = get_var('SECTION', 'base')
    pkg['metadata']['title'] = pkg['name']
    pkg['metadata']['maintainer'] = get_var('PKG_MAINTAINER', 'OpenWrt Developers')
    
    return pkg


def generate_yaml(pkg: Dict[str, Any]) -> str:
    """Generate YAML output from parsed package data."""
    lines = []
    
    def add_line(line: str = ''):
        lines.append(line)
    
    def add_comment(text: str):
        add_line(f'# {text}')
    
    # Header
    add_comment(f'{pkg["name"]} package definition')
    add_comment('Auto-generated from OpenWrt Makefile - review and adjust as needed')
    add_line()
    
    # Basic info
    add_line(f'name: {pkg["name"]}')
    add_line(f'version: "{pkg["version"]}"')
    add_line(f'release: {pkg["release"]}')
    add_line(f'license: {pkg["license"]}')
    add_line()
    
    # Source
    add_line('source:')
    src = pkg['source']
    if src.get('type') == 'git':
        add_line(f'  type: git')
        add_line(f'  url: "{src.get("url", "")}"')
        add_line(f'  version: "{src.get("version", "")}"')
        if src.get('sha256'):
            add_line(f'  sha256: "{src.get("sha256", "")}"')
    elif src.get('type') == 'tarball':
        add_line(f'  type: tarball')
        add_line(f'  url: "{src.get("url", "")}"')
        if src.get('sha256'):
            add_line(f'  sha256: "{src.get("sha256", "")}"')
    else:
        add_line('  type: local')
    add_line()
    
    # Metadata
    add_line('metadata:')
    add_line(f'  section: {pkg["metadata"].get("section", "base")}')
    if pkg['metadata'].get('category'):
        add_line(f'  category: {pkg["metadata"]["category"]}')
    add_line(f'  title: {pkg["metadata"].get("title", pkg["name"])}')
    if pkg['metadata'].get('maintainer'):
        add_line(f'  maintainer: {pkg["metadata"]["maintainer"]}')
    add_line()
    
    # Dependencies
    add_line('dependencies:')
    runtime_deps = []
    for subpkg in pkg['subpackages'].values():
        runtime_deps.extend(subpkg.get('runtime_deps', []))
    runtime_deps = list(set(runtime_deps))
    add_line(f'  runtime: {runtime_deps if runtime_deps else "[]"}')
    build_deps = pkg['dependencies'].get('build', [])
    add_line(f'  build: {build_deps if build_deps else "[]"}')
    add_line()
    
    # Build
    add_line('build:')
    add_line(f'  system: {pkg["build"].get("system", "make")}')
    add_line(f'  parallel: {str(pkg["build"].get("parallel", True)).lower()}')
    if pkg['build'].get('configure_args'):
        add_line('  configure_args:')
        for arg in pkg['build']['configure_args']:
            add_line(f'    - "{arg}"')
    add_line()
    
    # Subpackages
    if pkg['subpackages']:
        add_line('subpackages:')
        for name, subpkg in pkg['subpackages'].items():
            add_line(f'  {name}:')
            add_line(f'    description: "{subpkg.get("description", name)}"')
            add_line(f'    section: {subpkg.get("section", "base")}')
            
            if subpkg.get('files'):
                add_line('    files:')
                for f in subpkg['files']:
                    add_line(f'      - src: "{f["src"]}"')
                    add_line(f'        dst: "{f["dst"]}"')
                    add_line(f'        mode: "{f["mode"]}"')
            
            if subpkg.get('symlinks'):
                add_line('    symlinks:')
                for s in subpkg['symlinks']:
                    add_line(f'      - src: "{s["src"]}"')
                    add_line(f'        dst: "{s["dst"]}"')
    
    return '\n'.join(lines)


def main():
    parser = argparse.ArgumentParser(
        description='Convert OpenWrt package Makefile to YAML format'
    )
    parser.add_argument('makefile', type=Path, help='Path to OpenWrt Makefile')
    parser.add_argument('output', type=Path, nargs='?', help='Output YAML file (default: stdout)')
    parser.add_argument('-v', '--verbose', action='store_true', help='Verbose output')
    
    args = parser.parse_args()
    
    if not args.makefile.exists():
        print(f"Error: Makefile not found: {args.makefile}", file=sys.stderr)
        sys.exit(1)
    
    try:
        pkg = parse_makefile(args.makefile)
        yaml_content = generate_yaml(pkg)
        
        if args.output:
            # Create parent directory if it doesn't exist
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(yaml_content)
            print(f"Generated: {args.output}")
        else:
            print(yaml_content)
            
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        if args.verbose:
            import traceback
            traceback.print_exc()
        sys.exit(1)


if __name__ == '__main__':
    main()
