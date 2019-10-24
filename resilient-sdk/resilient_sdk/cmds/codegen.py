#!/usr/bin/env python
# -*- coding: utf-8 -*-
# (c) Copyright IBM Corp. 2010, 2019. All Rights Reserved.

""" Implementation of `resilient-sdk codegen` """

import logging
import os
from resilient import ensure_unicode
from resilient_sdk.cmds.base_cmd import BaseCmd
from resilient_sdk.util.sdk_exception import SDKException
from resilient_sdk.util.helpers import (get_resilient_client, setup_jinja_env,
                                        is_valid_package_name, write_file,
                                        validate_dir_paths, get_latest_org_export,
                                        get_from_export, minify_export,
                                        get_object_api_names, validate_file_paths,
                                        load_py_module, rename_file, rename_to_bak_file)

# Get the same logger object that is used in app.py
LOG = logging.getLogger("resilient_sdk_log")

# Relative paths from with the package of files + directories used
PATH_CUSTOMIZE_PY = os.path.join("util", "customize.py")


class CmdCodegen(BaseCmd):
    """TODO Docstring"""

    CMD_NAME = "codegen"
    CMD_HELP = "Generate boilerplate code to start developing an Extension"
    CMD_USAGE = "resilient-sdk codegen -p <name_of_package> -m <message_destination>"
    CMD_DESCRIPTION = "Generate boilerplate code to start developing an Extension"
    CMD_USE_COMMON_PARSER_ARGS = True

    def setup(self):
        # Define codegen usage and description
        self.parser.usage = self.CMD_USAGE
        self.parser.description = self.CMD_DESCRIPTION

        # Add any positional or optional arguments here
        self.parser.add_argument("-p", "--package",
                                 type=ensure_unicode,
                                 help="Name of the package to generate or path to existing package")

        self.parser.add_argument("--reload",
                                 action="store_true",
                                 help="Reload customizations and create new customize.py")

    def execute_command(self, args):
        LOG.debug("called: CmdCodegen.execute_command()")
        # Set command name in our SDKException class
        SDKException.command_ran = self.CMD_NAME

        LOG.debug("Getting resilient_client")

        # Instansiate connection to the Resilient Appliance
        res_client = get_resilient_client()

        if args.reload:
            if not args.package:
                raise SDKException("'-p' must be specified when using '--reload'")

            self._reload_package(res_client, args)

        elif args.package:
            self._gen_package(res_client, args)

        elif not args.package and args.function:
            self._gen_function(res_client, args)

        else:
            self.parser.print_help()

    @staticmethod
    def render_jinja_mapping(jinja_mapping_dict, jinja_env, target_dir):
        """
        Write all the Jinja Templates specified in jinja_mapping_dict that
        are found in the jinja_env to the target_dir

        :param jinja_mapping_dict: e.g. {"file_to_write.py": ("name_of_template.py.jinja2", jinja_data)}
        :param jinja_env: Jinja Environment
        :param target_dir: Path to write Templates to
        """
        for (file_name, file_info) in jinja_mapping_dict.items():

            if isinstance(file_info, dict):
                # This is a sub directory
                sub_dir_mapping_dict = file_info
                path_sub_dir = os.path.join(target_dir, file_name)

                try:
                    os.makedirs(path_sub_dir)
                except OSError as err_msg:
                    LOG.warn(err_msg)

                CmdCodegen.render_jinja_mapping(sub_dir_mapping_dict, jinja_env, path_sub_dir)

            else:
                # Get path to Jinja2 template
                path_template = file_info[0]

                # Get data dict for this Jinja2 template
                template_data = file_info[1]

                target_file = os.path.join(target_dir, file_name)

                if os.path.exists(target_file):
                    LOG.warning(u"File already exists. Not writing: %s", target_file)
                    continue

                jinja_template = jinja_env.get_template(path_template)
                jinja_rendered_text = jinja_template.render(template_data)

                write_file(target_file, jinja_rendered_text)

    @staticmethod
    def merge_codegen_params(old_params, args, mapping_tuples):
        """
        Merge any codegen params found in old_params and args.
        Return updated args

        :param old_params: List of Resilient Objects (normally result of calling customize_py.codegen_reload_data())
        :type old_params: List
        :param args: Namespace of all args passed from command line
        :type args: argparse.Namespace
        :param mapping_tuples: List of Tuples e.g. [("arg_name", "old_param_name")]
        :type mapping_tuples: List
        :return: The 'merged' args
        :rtype: argparse.Namespace
        """
        for m in mapping_tuples:
            all_obj_names_wanted = set()

            arg_name = m[0]
            old_param_name = m[1]

            arg = getattr(args, arg_name)
            if arg:
                all_obj_names_wanted = set(arg)

            setattr(args, arg_name, list(all_obj_names_wanted.union(set(old_params.get(old_param_name)))))

        return args

    @staticmethod
    def _gen_function(res_client, args):
        # TODO: Handle just generating a FunctionComponent for the /components directory
        LOG.info("codegen _gen_function called")

    @staticmethod
    def _gen_package(res_client, args):
        LOG.info("codegen _gen_package called")

        if not is_valid_package_name(args.package):
            raise SDKException(u"'{0}' is not a valid package name".format(args.package))

        package_name = args.package

        # Get output_base, use args.output if defined, else current directory
        output_base = args.output if args.output else os.curdir
        output_base = os.path.abspath(output_base)

        # TODO: handle being passed path to an actual export.res file
        org_export = get_latest_org_export(res_client)

        # Get data required for Jinja2 templates from export
        jinja_data = get_from_export(org_export,
                                     message_destinations=args.messagedestination,
                                     functions=args.function,
                                     workflows=args.workflow,
                                     rules=args.rule,
                                     fields=args.field,
                                     artifact_types=args.artifacttype,
                                     datatables=args.datatable,
                                     tasks=args.task,
                                     scripts=args.script)

        # Get 'minified' version of the export. This is used in customize.py
        jinja_data["export_data"] = minify_export(org_export,
                                                  message_destinations=get_object_api_names("x_api_name", jinja_data.get("message_destinations")),
                                                  functions=get_object_api_names("x_api_name", jinja_data.get("functions")),
                                                  workflows=get_object_api_names("x_api_name", jinja_data.get("workflows")),
                                                  rules=get_object_api_names("x_api_name", jinja_data.get("rules")),
                                                  fields=jinja_data.get("all_fields"),
                                                  artifact_types=get_object_api_names("x_api_name", jinja_data.get("artifact_types")),
                                                  datatables=get_object_api_names("x_api_name", jinja_data.get("datatables")),
                                                  tasks=get_object_api_names("x_api_name", jinja_data.get("tasks")),
                                                  phases=get_object_api_names("x_api_name", jinja_data.get("phases")),
                                                  scripts=get_object_api_names("x_api_name", jinja_data.get("scripts")))

        # Add package_name to jinja_data
        jinja_data["package_name"] = package_name

        # Validate we have write permissions
        validate_dir_paths(os.W_OK, output_base)

        # Join package_name to output base
        output_base = os.path.join(output_base, package_name)

        # If the output_base directory does not exist, create it
        if not os.path.exists(output_base):
            os.makedirs(output_base)

        # Instansiate Jinja2 Environment with path to Jinja2 templates
        jinja_env = setup_jinja_env("data/codegen/templates/package_template")

        # This dict maps our package file structure to  Jinja2 templates
        package_mapping_dict = {
            "MANIFEST.in": ("MANIFEST.in.jinja2", jinja_data),
            "README.md": ("README.md.jinja2", jinja_data),
            "setup.py": ("setup.py.jinja2", jinja_data),
            "tox.ini": ("tox.ini.jinja2", jinja_data),

            package_name: {
                "__init__.py": ("package/__init__.py.jinja2", jinja_data),
                "LICENSE": ("package/LICENSE.jinja2", jinja_data),

                "components": {
                    "__init__.py": ("package/components/__init__.py.jinja2", jinja_data),
                },
                "util": {
                    "__init__.py": ("package/util/__init__.py.jinja2", jinja_data),
                    "config.py": ("package/util/config.py.jinja2", jinja_data),
                    "customize.py": ("package/util/customize.py.jinja2", jinja_data),
                    "selftest.py": ("package/util/selftest.py.jinja2", jinja_data),
                }
            }
        }

        # If there are Functions, add a 'tests' directory
        if jinja_data.get("functions"):
            package_mapping_dict["tests"] = {}

        # Loop each Function
        for f in jinja_data.get("functions"):
            # Add package_name to function data
            f["package_name"] = package_name

            # Generate function_component.py file name
            file_name = u"funct_{0}.py".format(f.get("export_key"))

            # Add to 'components' directory
            package_mapping_dict[package_name]["components"][file_name] = ("package/components/function.py.jinja2", f)

            # Add to 'tests' directory
            package_mapping_dict["tests"][u"test_{0}".format(file_name)] = ("tests/test_function.py.jinja2", f)

        CmdCodegen.render_jinja_mapping(package_mapping_dict, jinja_env, output_base)

    @staticmethod
    def _reload_package(res_client, args):
        LOG.debug("called: CmdCodegen._reload_package()")

        old_params, path_customize_py_bak = [], ""

        # Get + validate package and customize.py paths
        path_package = os.path.abspath(args.package)
        validate_dir_paths(os.R_OK, path_package)

        path_customize_py = os.path.join(path_package, os.path.basename(path_package), PATH_CUSTOMIZE_PY)
        validate_file_paths(os.W_OK, path_customize_py)

        # Load customize module
        customize_py = load_py_module(path_customize_py, "customize")

        try:
            # Get the 'old_params' from customize.py
            old_params = customize_py.codegen_reload_data()
        except AttributeError:
            raise SDKException(u"Corrupt customize.py. No reload method found in {0}".format(path_customize_py))

        if not old_params:
            raise SDKException(u"No reload params found in {0}".format(path_customize_py))

        # Rename the old customize.py with .bak
        path_customize_py_bak = rename_to_bak_file(path_customize_py)

        try:
            # Map command line arg name to dict key return by codegen_reload_data() in customize.py
            mapping_tuples = [
                ("messagedestination", "message_destinations"),
                ("function", "functions"),
                ("workflow", "workflows"),
                ("rule", "actions"),
                ("field", "incident_fields"),
                ("artifacttype", "incident_artifact_types"),
                ("datatable", "datatables"),
                ("task", "automatic_tasks"),
                ("script", "scripts")
            ]

            # Merge old_params with new params specified on command line
            args = CmdCodegen.merge_codegen_params(old_params, args, mapping_tuples)

            LOG.debug("Regenerating codegen '%s' package now", args.package)

            # Regenerate the package
            CmdCodegen._gen_package(res_client, args)

        except Exception as err:
            LOG.error(u"Error running resilient-sdk codegen --reload\n\nERROR:%s", err)

        finally:
            # If an error occurred, customize.py does not exist, rename the backup file to original
            if not os.path.isfile(path_customize_py):
                LOG.info(u"An error occurred. Renaming customize.py.bak to customize.py")
                rename_file(path_customize_py_bak, "customize.py")
