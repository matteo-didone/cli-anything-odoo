from setuptools import find_namespace_packages, setup

with open("README.md", "r", encoding="utf-8") as fh:
    long_description = fh.read()

setup(
    name="cli-anything-odoo",
    version="1.0.0",
    description="Agent-native CLI for Odoo 18: ORM, modules and databases over XML-RPC",
    long_description=long_description,
    long_description_content_type="text/markdown",
    url="https://github.com/matteo-didone/cli-anything-odoo",
    license="MIT",
    packages=find_namespace_packages(include=["cli_anything.*"]),
    include_package_data=True,
    package_data={"cli_anything.odoo": ["skills/*.md"]},
    python_requires=">=3.10",
    install_requires=["click>=8.0", "prompt_toolkit>=3.0"],
    extras_require={"dev": ["pytest>=7.0"]},
    entry_points={
        "console_scripts": [
            "cli-anything-odoo=cli_anything.odoo.odoo_cli:main",
        ],
    },
    classifiers=[
        "Development Status :: 4 - Beta",
        "Environment :: Console",
        "Intended Audience :: Developers",
        "License :: OSI Approved :: MIT License",
        "Programming Language :: Python :: 3",
        "Topic :: Office/Business",
        "Topic :: Software Development :: Libraries :: Python Modules",
    ],
)
