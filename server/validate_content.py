"""Validate all content: python -m server.validate_content"""

import sys

from . import content


def main():
    try:
        content.load_assessment()
        print("assessment-v1: OK")
        for p in content.list_packages():
            print(f"{p['package_id']}@{p['content_version']}: OK  ({p['title']})")
    except content.ContentError as e:
        print(f"INVALID: {e}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
