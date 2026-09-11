"""Allow `python -m cli_anything.odoo`, which the subprocess tests fall back to."""

from .odoo_cli import main

if __name__ == "__main__":
    main()
