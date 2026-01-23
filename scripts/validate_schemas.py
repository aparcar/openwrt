#!/usr/bin/env python3
"""Validate all package.yaml and target.yaml files against their JSON schemas.

Usage:
    uv run scripts/validate_schemas.py [--verbose] [--fix]
    python3 scripts/validate_schemas.py [--verbose] [--fix]

Options:
    --verbose, -v    Show all files being validated
    --fix           Attempt to fix common schema issues (not implemented yet)
    --packages      Only validate package.yaml files
    --targets       Only validate target.yaml files
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Optional

try:
    import jsonschema
    import yaml
except ImportError:
    print("Error: Required packages not found. Install with:")
    print("  uv pip install jsonschema pyyaml")
    print("  # or")
    print("  pip install jsonschema pyyaml")
    sys.exit(1)


class SchemaValidator:
    """Validates YAML files against JSON schemas."""

    def __init__(self, schema_dir: Path, verbose: bool = False):
        self.schema_dir = schema_dir
        self.verbose = verbose
        self.package_schema = self._load_schema("package.schema.json")
        self.target_schema = self._load_schema("target.schema.json")
        self.config_schema = self._load_schema("config.schema.json")

        self.errors: list[tuple[Path, str]] = []
        self.warnings: list[tuple[Path, str]] = []
        self.valid_count = 0
        self.invalid_count = 0

    def _load_schema(self, filename: str) -> Optional[dict]:
        """Load a JSON schema file."""
        schema_path = self.schema_dir / filename
        if not schema_path.exists():
            print(f"Warning: Schema file not found: {schema_path}")
            return None
        try:
            return json.loads(schema_path.read_text())
        except json.JSONDecodeError as e:
            print(f"Error: Invalid JSON in {schema_path}: {e}")
            return None

    def validate_file(self, yaml_path: Path, schema: dict, schema_name: str) -> bool:
        """Validate a single YAML file against a schema."""
        try:
            data = yaml.safe_load(yaml_path.read_text())
            if data is None:
                self.warnings.append((yaml_path, "Empty YAML file"))
                return True

            jsonschema.validate(data, schema)
            self.valid_count += 1
            if self.verbose:
                print(f"  \033[32m✓\033[0m {yaml_path}")
            return True

        except yaml.YAMLError as e:
            self.errors.append((yaml_path, f"YAML parse error: {e}"))
            self.invalid_count += 1
            print(f"  \033[31m✗\033[0m {yaml_path}: YAML parse error")
            return False

        except jsonschema.ValidationError as e:
            # Format error message
            path_str = " -> ".join(str(p) for p in e.absolute_path) if e.absolute_path else "root"
            msg = f"{e.message} (at {path_str})"
            self.errors.append((yaml_path, msg))
            self.invalid_count += 1
            print(f"  \033[31m✗\033[0m {yaml_path}")
            print(f"      {e.message}")
            if e.absolute_path:
                print(f"      Path: {path_str}")
            return False

    def validate_packages(self, package_dir: Path) -> int:
        """Validate all package.yaml files."""
        if not self.package_schema:
            print("Skipping package validation (no schema)")
            return 0

        print("\nValidating package.yaml files...")
        count = 0

        for yaml_path in sorted(package_dir.rglob("package.yaml")):
            # Skip build directories
            if "/build/" in str(yaml_path) or "/.git/" in str(yaml_path):
                continue
            count += 1
            self.validate_file(yaml_path, self.package_schema, "package")

        print(f"  Validated {count} package files")
        return count

    def validate_targets(self, target_dir: Path) -> int:
        """Validate all target.yaml files."""
        if not self.target_schema:
            print("Skipping target validation (no schema)")
            return 0

        print("\nValidating target.yaml files...")
        count = 0

        for yaml_path in sorted(target_dir.rglob("target.yaml")):
            # Skip build directories
            if "/build/" in str(yaml_path) or "/.git/" in str(yaml_path):
                continue
            count += 1
            self.validate_file(yaml_path, self.target_schema, "target")

        print(f"  Validated {count} target files")
        return count

    def validate_config(self, config_path: Path) -> bool:
        """Validate a config.yaml file."""
        if not self.config_schema:
            print("Skipping config validation (no schema)")
            return True

        if not config_path.exists():
            return True

        print(f"\nValidating {config_path}...")
        return self.validate_file(config_path, self.config_schema, "config")

    def print_summary(self):
        """Print validation summary."""
        print("\n" + "=" * 60)
        print("VALIDATION SUMMARY")
        print("=" * 60)
        print(f"  Valid:   {self.valid_count}")
        print(f"  Invalid: {self.invalid_count}")
        print(f"  Warnings: {len(self.warnings)}")

        if self.errors:
            print(f"\n\033[31mErrors ({len(self.errors)}):\033[0m")
            for path, msg in self.errors[:20]:  # Show first 20 errors
                print(f"  {path}: {msg}")
            if len(self.errors) > 20:
                print(f"  ... and {len(self.errors) - 20} more errors")

        if self.warnings:
            print(f"\n\033[33mWarnings ({len(self.warnings)}):\033[0m")
            for path, msg in self.warnings[:10]:
                print(f"  {path}: {msg}")


def main():
    parser = argparse.ArgumentParser(description="Validate YAML files against JSON schemas")
    parser.add_argument("-v", "--verbose", action="store_true", help="Show all validated files")
    parser.add_argument("--packages", action="store_true", help="Only validate packages")
    parser.add_argument("--targets", action="store_true", help="Only validate targets")
    parser.add_argument("--config", type=Path, help="Validate a specific config file")
    args = parser.parse_args()

    # Find project root
    script_dir = Path(__file__).parent
    project_root = script_dir.parent

    schema_dir = project_root / "owrt" / "schema"
    package_dir = project_root / "package"
    target_dir = project_root / "target"

    if not schema_dir.exists():
        print(f"Error: Schema directory not found: {schema_dir}")
        sys.exit(1)

    validator = SchemaValidator(schema_dir, verbose=args.verbose)

    # Default to validating both if neither specified
    validate_packages = args.packages or (not args.packages and not args.targets)
    validate_targets = args.targets or (not args.packages and not args.targets)

    if validate_packages and package_dir.exists():
        validator.validate_packages(package_dir)

    if validate_targets and target_dir.exists():
        validator.validate_targets(target_dir)

    if args.config:
        validator.validate_config(args.config)

    validator.print_summary()

    # Exit with error code if there were validation errors
    sys.exit(1 if validator.invalid_count > 0 else 0)


if __name__ == "__main__":
    main()
