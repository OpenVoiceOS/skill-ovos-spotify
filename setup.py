from setuptools import setup
import os
from os import walk, path


# update this!
URL = "https://github.com/forslund/skill-spotify"
SKILL_CLAZZ = "SkillSpotify"  # needs to match __init__.py class name
PYPI_NAME = "skill-spotify"  # pip install PYPI_NAME


SKILL_AUTHOR = "forslund"
SKILL_NAME = "skill-spotify"
SKILL_PKG = SKILL_NAME.lower().replace('-', '_')
PLUGIN_ENTRY_POINT = f'{SKILL_NAME.lower()}.{SKILL_AUTHOR.lower()}={SKILL_PKG}:{SKILL_CLAZZ}'



def get_requirements(requirements_filename: str):
    requirements_file = path.join(path.abspath(path.dirname(__file__)),
                                  requirements_filename)
    with open(requirements_file, 'r', encoding='utf-8') as r:
        requirements = r.readlines()
    requirements = [r.strip() for r in requirements if r.strip()
                    and not r.strip().startswith("#")]
    if 'MYCROFT_LOOSE_REQUIREMENTS' in os.environ:
        print('USING LOOSE REQUIREMENTS!')
        requirements = [r.replace('==', '>=').replace('~=', '>=') for r in requirements]
    return requirements


def find_resource_files():
    # add any folder with files your skill uses here! 
    resource_base_dirs = ("locale", "ui", "vocab", "dialog", "regex")
    base_dir = path.dirname(__file__)
    package_data = ["*.json"]
    for res in resource_base_dirs:
        print(path.join(base_dir, res))
        if path.isdir(path.join(base_dir, res)):
            print("IS DIR!")
            for (directory, _, files) in walk(path.join(base_dir, res)):
                if files:
                    package_data.append(
                        path.join(directory.replace(base_dir, "").lstrip('/'),
                                  '*'))
    print(package_data)
    return package_data


# TODO - add description, author, email, license, etc
setup(
    # this is the package name that goes on pip
    name=PYPI_NAME,
    version="0.1.1",
    url=URL,
    license='Apache-2.0',
    package_data={SKILL_PKG: ['locale/**/*']},
    packages=[SKILL_PKG],
    include_package_data=True,
#    install_requires=get_requirements("requirements.txt"),
    keywords='ovos skill plugin',
    entry_points={'ovos.plugin.skill': PLUGIN_ENTRY_POINT}
)
