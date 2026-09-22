from setuptools import setup, find_packages

setup(
    name='trading-platform',
    version='1.0.0',
    description='Trading Strategy Execution Platform for Indian Markets',
    author='Trading Platform Team',
    packages=find_packages(),
    install_requires=[
        'pyyaml',
    ],
    python_requires='>=3.8',
    entry_points={
        'console_scripts': [
            'trading-platform=cli:main',
        ],
    },
)
