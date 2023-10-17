#!/usr/bin/env python
# -*- coding: utf-8 -*-
# (c) Copyright IBM Corp. 2010, 2023. All Rights Reserved.

""" Implementation of 'resilient-sdk docgen' """

import logging
import os
import re
import shutil

from resilient_sdk.cmds.base_cmd import BaseCmd
from resilient_sdk.util import constants
from resilient_sdk.util import package_file_helpers as package_helpers
from resilient_sdk.util import sdk_helpers
from resilient_sdk.util.resilient_objects import (IGNORED_INCIDENT_FIELDS,
                                                  ResilientObjMap)
from resilient_sdk.util.sdk_exception import SDKException

from resilient import ensure_unicode

# Get the same logger object that is used in app.py
LOG = logging.getLogger(constants.LOGGER_NAME)

# JINJA Constants
README_TEMPLATE_NAME = "README.md.jinja2"


class CmdDocgen(BaseCmd):
    """
    Create a README.md for the specified app. Reads all details from
    the ImportDefinition in the customize.py file. Creates a backup of
    of the README.md if one exists already. The README.md is
    really an 'inventory' of what the app contains and details for
    the app configs
    """

    CMD_NAME = "docgen"
    CMD_HELP = "Generates boilerplate documentation for an app."
    CMD_USAGE = """
    $ resilient-sdk docgen -p <path_to_package>
    $ resilient-sdk docgen -p <name_of_package> --settings <path_to_custom_sdk_settings_file>
    $ resilient-sdk docgen -p <name_of_package> --poller # for a poller app
    $ resilient-sdk docgen -e export.res
    $ resilient-sdk docgen -e playbook1.resz playbook2.resz -o /path/to/save/playbooks_readme.md
    $ resilient-sdk docgen -e . -o README.md # reads all exports in current directory and outputs to README.md"""
    CMD_DESCRIPTION = CMD_HELP
    CMD_ADD_PARSERS = [constants.SDK_SETTINGS_PARSER_NAME]

    def setup(self):
        # Define docgen usage and description
        self.parser.usage = self.CMD_USAGE
        self.parser.description = self.CMD_DESCRIPTION

        # Add any positional or optional arguments here
        self.parser.add_argument("-p", "--package",
                                 type=ensure_unicode,
                                 help="Path to the package containing the setup.py file",
                                 nargs="?",
                                 default=os.getcwd())

        self.parser.add_argument("-e", "--export",
                                 type=ensure_unicode,
                                 nargs="+",
                                 help="List of files or directory containing export.res or export.resz file generated by resilient-sdk extract or exported from the SOAR platform. Ignored when used in conjunction with '--package'")

        self.parser.add_argument("-o", "--output",
                                 type=ensure_unicode,
                                 nargs="?",
                                 help="Full or relative path indicating where to save the file that docgen generates")

        self.parser.add_argument("-pr", "--poller",
                                 action="store_true",
                                 help="Include poller section in README generated by docgen")

    @staticmethod
    def _get_fn_input_details(function):
        """Return a List of all Function Inputs which are Dictionaries with
        the attributes: api_name, name, type, required, placeholder and tooltip"""

        fn_inputs = []

        for i in function.get("inputs", []):
            the_input = {}

            the_input["api_name"] = i.get("name")
            the_input["name"] = i.get("text")
            the_input["type"] = i.get("input_type")
            the_input["required"] = "Yes" if "always" in i.get("required", "") else "No"
            the_input["placeholder"] = i.get("placeholder") if i.get("placeholder") else "-"
            the_input["tooltip"] = i.get("tooltip") if i.get("tooltip") else "-"

            fn_inputs.append(the_input)

        fn_inputs = sorted(fn_inputs, key=lambda i: i["api_name"])

        return fn_inputs

    @classmethod
    def _get_function_details(cls, export):
        """
        Return a List of Functions which are Dictionaries with
        the attributes: name, simple_name, anchor, description, uuid, inputs,
        workflows, pre_processing_script, post_processing_script.

        The scripts are looked for first in playbooks as since v50
        that is the preferred way for apps to use functions now.
        If playbook scripts associated with the function are not found,
        they are searched for in workflows.
        """

        return_list = []

        functions = export.get("functions")
        workflows = export.get("workflows")
        playbooks = export.get("playbooks")
        scripts = export.get("scripts")

        for fn in functions:
            the_function = {}

            the_function["name"] = fn.get("display_name")
            the_function["simple_name"] = sdk_helpers.simplify_string(the_function.get("name"))
            the_function["anchor"] = sdk_helpers.generate_anchor(the_function.get("name"))
            the_function["description"] = fn.get("description")["content"]
            the_function["uuid"] = fn.get("uuid")
            the_function["inputs"] = cls._get_fn_input_details(fn)
            the_function["message_destination"] = fn.get("destination_handle", "")
            the_function["workflows"] = fn.get("workflows", [])
            the_function["x_api_name"] = fn.get("x_api_name", "")

            # look for pre/post scripts in playbooks first
            pre_script, post_script = cls._get_pre_and_post_processing_scripts_from_playbooks(the_function, playbooks, scripts, export)
            # if not found in playbooks, then look in workflows
            if not pre_script and not post_script:
                pre_script, post_script = cls._get_pre_and_post_processing_scripts_from_workflows(the_function, workflows)

            # save the scripts for jinja to use later
            the_function["pre_processing_script"] = pre_script
            the_function["post_processing_script"] = post_script

            return_list.append(the_function)

        return return_list

    @staticmethod
    def _get_pre_and_post_processing_scripts_from_playbooks(the_function, playbooks, scripts, export):
        """
        Look through all playbooks in the export, searching for any that
        use the function in question. If any do, if the function instance
        uses a pre-processing script, use that as the pre-processing script.
        If any scripts in the playbook reference the output of the function
        in their script (we do a very specific search which looks for Python
        code (not comments) that reference the specific string 
        ``playbook.functions.results.<output_name>``), then use that script
        as an example post-processing script. If none are found in local
        scripts, search the global scripts of the export

        :param the_function: the function in question for which we want example scripts
        :type the_function: dict
        :param playbooks: list of playbooks in the export
        :type playbooks: list[dict]
        :param playbooks: list of global scripts in the export
        :type playbooks: list[dict]
        :return: pre and post processing scripts as text
        :rtype: tuple(str, str)
        """
        pre_script, post_script = None, None

        for playbook in playbooks:
            # This gets all the functions and scripts in the Playbooks's XML
            pb_objects = sdk_helpers.get_playbook_objects(playbook)

            # loop through the playbook to find its functions
            for pb_fn in pb_objects.get("functions", []):
                # the best proxy we have for "post processing" script is
                # if the output name of the function is used in the script's
                # code. we check for that there and if so, we've found a
                # "post processing" script

                # This regex searches for Python code instances of the 
                # output. It is relatively robust and will not match commented code
                # nor non-exact matches
                # For examples on this regex, see: https://regex101.com/r/Fv5AQm
                regex_str = r"^(?!#).*playbook\.functions\.results\.{0}\b.*".format(pb_fn.get("result_name"))
                regex_compiled = re.compile(regex_str, re.MULTILINE)


                # if the pb_fn matches our goal fn:
                if pb_fn.get("uuid", "uuid_not_found_pb") == the_function.get("uuid", "uuid_not_found_fn"):
                    if not pre_script: # use the first pre-script found
                        pre_script = pb_fn["pre_processing_script"]

                    # loop through local scripts looking for a match
                    for pb_sc in pb_objects.get("scripts", []):
                        script_is_found = sdk_helpers.get_script_info(pb_sc, playbook.get("local_scripts"), sdk_helpers.SCRIPT_TYPE_MAP.get("local"))

                        if not post_script and script_is_found and regex_compiled.search(pb_sc.get("script_text")) is not None:
                            post_script = pb_sc.get("script_text")

                        # if we didn't find a local script, we can run the same search on
                        # global scripts and we might get a hit
                        if not script_is_found:
                            for g_sc in sdk_helpers.get_res_obj("scripts", "uuid", "Script", [pb_sc.get("uuid")], export):
                                if not post_script and regex_compiled.search(g_sc.get("script_text")) is not None:
                                    post_script = g_sc.get("script_text")

                    # short the searching if we've already found both
                    if pre_script and post_script:
                        return pre_script, post_script

        return pre_script, post_script

    @staticmethod
    def _get_pre_and_post_processing_scripts_from_workflows(the_function, workflows):
        """
        Get pre and post processing scripts from workflows.
        Search the list of workflows for a match with the function in question.
        If found, return the pre and post processing scripts. Note that it will
        only "short-circuit" return if both are found. So that could mean that
        if a function is used twice in an export, but only has a post processing
        script in the second workflow, docgen would take the pre processing
        script of the first instance and the post script of the second.

        :param the_function: the function in question for which we want example scripts
        :type the_function: dict
        :param workflows: list of workflows in the export
        :type workflows: list[dict]
        :return: pre and post processing scripts as text
        :rtype: tuple(str, str)
        """
        pre_script, post_script = None, None

        # Loop the Function's associated Workflows
        for fn_wf in the_function.get("workflows"):

            fn_wf_name = fn_wf.get(ResilientObjMap.WORKFLOWS)

            # Loop all Workflow Objects
            for wf in workflows:

                # Find a match
                if fn_wf_name == wf.get(ResilientObjMap.WORKFLOWS):

                    # Get List of Function details from Workflow XML
                    workflow_functions = sdk_helpers.get_workflow_functions(wf, the_function.get("uuid"))

                    # Get a valid pre and post process script, then break
                    for a_fn in workflow_functions:

                        if not pre_script:
                            pre_script = a_fn.get("pre_processing_script")

                        if not post_script:
                            post_script = a_fn.get("post_processing_script")

                        if pre_script and post_script:
                            return pre_script, post_script

        # return one or the other (or both None) if not both were found
        # here to maintain same functionality that was there before this was moved out. see PR #643
        return pre_script, post_script

    @staticmethod
    def _get_script_details(scripts):
        """Return a List of all Scripts which are Dictionaries with
        the attributes: name, simple_name, description, object_type, script_text"""

        return_list = []

        for s in scripts:
            the_script = {}

            the_script["name"] = s.get("name")
            the_script["simple_name"] = sdk_helpers.simplify_string(the_script.get("name"))
            the_script["anchor"] = sdk_helpers.generate_anchor(the_script.get("name"))
            the_script["description"] = s.get("description")
            the_script["object_type"] = s.get("object_type")
            the_script["script_text"] = s.get("script_text")
            return_list.append(the_script)

        return return_list

    @staticmethod
    def _get_rule_details(rules):
        """Return a List of all Rules which are Dictionaries with
        the attributes: name, simple_name, object_type and workflow_triggered"""

        return_list = []

        for rule in rules:
            the_rule = {}

            the_rule["name"] = rule.get("name")
            the_rule["simple_name"] = sdk_helpers.simplify_string(the_rule.get("name"))
            the_rule["object_type"] = rule.get("object_type", "")

            rule_workflows = rule.get("workflows", [])
            the_rule["workflow_triggered"] = rule_workflows[0] if rule_workflows else "-"

            the_rule["conditions"] = sdk_helpers.str_repr_activation_conditions(rule) or "-"

            return_list.append(the_rule)

        return return_list

    @staticmethod
    def _get_playbook_details(playbooks):
        """Return a List of all Playbooks which are Dictionaries with
        the attributes: api_name, name, object_type, status and description"""

        return_list = []

        for playbook in playbooks:
            the_playbook = {}

            the_playbook["api_name"] = playbook.get("x_api_name")
            the_playbook["name"] = playbook.get("display_name")
            the_playbook["object_type"] = playbook.get("object_type", "")
            the_playbook["status"] = playbook.get("status", "")
            the_playbook["description"] = playbook.get("description", {}).get("content", "")
            the_playbook["activation_type"] = playbook.get("activation_type", "")
            the_playbook["conditions"] = playbook.get("conditions", "")
            the_playbook["version"] = playbook.get("version")

            return_list.append(the_playbook)

        return return_list

    @staticmethod
    def _get_datatable_details(datatables):
        """Return a List of all Data Tables which are Dictionaries with
        the attributes: name, anchor, api_name, and columns. Columns is
        also a List of Dictionaries that has the attributes: name,
        api_name, type and tooltip"""

        return_list = []

        for datatable in datatables:
            the_dt = {}

            the_dt["name"] = datatable.get("display_name")
            the_dt["simple_name"] = sdk_helpers.simplify_string(the_dt.get("name"))
            the_dt["anchor"] = sdk_helpers.generate_anchor(the_dt.get("name"))
            the_dt["api_name"] = datatable.get("type_name")

            the_dt_columns = []

            # datatable.fields is a Dict where its values (the columns) also Dicts
            for col in datatable.get("fields", {}).values():
                the_col = {}

                the_col["name"] = col.get("text")
                the_col["api_name"] = col.get("name")
                the_col["type"] = col.get("input_type")
                the_col["tooltip"] = col.get("tooltip") if col.get("tooltip") else "-"

                the_dt_columns.append(the_col)

            the_dt["columns"] = sorted(the_dt_columns, key=lambda c: c["api_name"])

            return_list.append(the_dt)

        return return_list

    @staticmethod
    def _get_custom_fields_details(fields):
        """Return a List of all Custom Incident Fields which are Dictionaries with
        the attributes: api_name, label, type, prefix, placeholder and tooltip"""
        return_list = []

        for field in fields:
            the_field = {}

            the_field["api_name"] = field.get("name")
            the_field["label"] = field.get("text")
            the_field["type"] = field.get("input_type")
            the_field["prefix"] = field.get("prefix")
            the_field["placeholder"] = field.get("placeholder") if field.get("placeholder") else "-"
            the_field["tooltip"] = field.get("tooltip") if field.get("tooltip") else "-"

            return_list.append(the_field)

        return return_list

    @staticmethod
    def _get_custom_artifact_details(custom_artifact_types):
        """Return a List of all Custom Incident Artifact Types which are Dictionaries with
        the attributes: api_name, display_name and description"""
        return_list = []

        for artifact_type in custom_artifact_types:
            the_artifact_type = {}

            the_artifact_type["api_name"] = artifact_type.get("programmatic_name")
            the_artifact_type["display_name"] = artifact_type.get("name")
            the_artifact_type["description"] = artifact_type.get("desc")

            return_list.append(the_artifact_type)

        return return_list

    @staticmethod
    def _get_poller_details(path_to_src, package_name):
        """Return the contents of each template file for a poller"""
        file_names = [
            package_helpers.BASE_NAME_POLLER_CREATE_CASE_TEMPLATE,
            package_helpers.BASE_NAME_POLLER_UPDATE_CASE_TEMPLATE,
            package_helpers.BASE_NAME_POLLER_CLOSE_CASE_TEMPLATE
        ]

        contents = {}

        for f in file_names:
            file_path = os.path.join(path_to_src, package_name, "poller", "data", f)
            try:
                sdk_helpers.validate_file_paths(os.R_OK, file_path)
                content = "".join(sdk_helpers.read_file(file_path))
                contents[f] = content
            except SDKException as err:
                sdk_helpers.handle_file_not_found_error(err,
                    u"Error getting poller template contents. No '{0}' file found.".format(f))

        return contents

    @staticmethod
    def _get_export_paths_from_args(export_path_list):
        """
        Given a list of paths (files or directories),
        return a unique list of absolute paths to each file
        and file in the directory (1st level of the directory).

        **Example:**

        .. code-block::

            cd ~/Desktop
            resilient-sdk docgen -e . some_file.resz

        translates to:

        .. code-block::

            >>> CmdDocgen._get_export_paths_from_args([".", "some_file.resz"])
            Skipping --export arg /Users/user/Desktop/some_file.resz because it is not a valid file or directory
            ['/Users/user/Desktop/export.res', '/Users/user/Desktop/test.txt']

        :param export_path_list: list of paths, either files or directories
        :type export_path_list: list
        :return: list of absolute paths to files from the given paths
        :rtype: list[str]
        """
        final_export_list = []
        for export_path in export_path_list:
            export_path = os.path.abspath(export_path)
            if os.path.isfile(export_path):
                final_export_list.append(export_path)
            elif os.path.isdir(export_path):
                for file_path in os.listdir(export_path):
                    file_path = os.path.join(export_path, file_path)
                    if os.path.isfile(file_path) and not os.path.basename(file_path).startswith("."):
                        final_export_list.append(file_path)
            else:
                LOG.warning("Skipping --export arg '%s' because it is not a valid file or directory", export_path)

        # cast to set then back to list so that uniqueness is guaranteed
        return list(set(final_export_list))

    @staticmethod
    def _add_payload_samples_to_functions(functions, path_payload_samples_dir):
        """
        For each function in the functions list, search the payload samples
        directory: if the matching payload for a function is found,
        add "results" object to the function (in place) which contains the JSON
        payload from the payload sample

        :param functions: list of SOAR functions objects
        :type functions: list[dict]
        :param path_payload_samples_dir: absolute path to payload samples directory
        :type path_payload_samples_dir: str
        """
        if not path_payload_samples_dir:
            return
        # See if a payload_samples dir exists and use the contents for function results
        try:
            sdk_helpers.validate_dir_paths(os.R_OK, path_payload_samples_dir)

            for f in functions:
                fn_name = f.get("x_api_name")
                path_payload_samples_fn_name = os.path.join(path_payload_samples_dir, fn_name)
                path_output_json_example = os.path.join(path_payload_samples_fn_name, package_helpers.BASE_NAME_PAYLOAD_SAMPLES_EXAMPLE)

                try:
                    sdk_helpers.validate_file_paths(os.R_OK, path_output_json_example)
                    f["results"] = sdk_helpers.read_json_file(path_output_json_example)
                except SDKException as e:
                    sdk_helpers.handle_file_not_found_error(e, u"Error getting results. No '{0}' file found for '{1}'.".format(
                        package_helpers.BASE_NAME_PAYLOAD_SAMPLES_EXAMPLE, fn_name))

        except SDKException as e:
            sdk_helpers.handle_file_not_found_error(e, u"Error getting results. No '{0}' directory found.".format(
                package_helpers.BASE_NAME_PAYLOAD_SAMPLES_EXAMPLE))

    def _get_all_objects_for_jinja_render(self, export_contents):
        """
        Take an export and gather the lists of SOAR objects to use to render
        the Jinja template properly

        :param export_contents: full export contents
        :type export_contents: dict
        :return: functions, scripts, rules, datatables, custom fields, artifact types,
            and playbooks from the export, each in a list of all the relevant objects
            found in the export and processed by the docgen internal processing functions
            which properly format and enhance each object to fit the template's requirements
        :rtype: tuple of seven list[dict]
        """
        # Get field names from ImportDefinition
        field_names = []
        for f in export_contents.get("fields", []):
            f_export_key = f.get("export_key")

            if "incident/" in f_export_key and f_export_key not in IGNORED_INCIDENT_FIELDS:
                field_names.append(f.get(ResilientObjMap.FIELDS, ""))

        # Get data from ImportDefinition
        import_def_data = sdk_helpers.get_from_export(export_contents,
            message_destinations=sdk_helpers.get_object_api_names(ResilientObjMap.MESSAGE_DESTINATIONS, export_contents.get("message_destinations")),
            functions=sdk_helpers.get_object_api_names(ResilientObjMap.FUNCTIONS, export_contents.get("functions")),
            workflows=sdk_helpers.get_object_api_names(ResilientObjMap.WORKFLOWS, export_contents.get("workflows")),
            rules=sdk_helpers.get_object_api_names(ResilientObjMap.RULES, export_contents.get("actions")),
            fields=field_names,
            artifact_types=sdk_helpers.get_object_api_names(ResilientObjMap.INCIDENT_ARTIFACT_TYPES, export_contents.get("incident_artifact_types")),
            datatables=sdk_helpers.get_object_api_names(ResilientObjMap.DATATABLES, export_contents.get("types")),
            tasks=sdk_helpers.get_object_api_names(ResilientObjMap.TASKS, export_contents.get("automatic_tasks")),
            scripts=sdk_helpers.get_object_api_names(ResilientObjMap.SCRIPTS, export_contents.get("scripts")),
            playbooks=sdk_helpers.get_object_api_names(ResilientObjMap.PLAYBOOKS, export_contents.get("playbooks", [])))

        # Lists we use in Jinja Templates
        functions = self._get_function_details(import_def_data)
        scripts = self._get_script_details(import_def_data.get("scripts", []))
        rules = self._get_rule_details(import_def_data.get("rules", []))
        datatables = self._get_datatable_details(import_def_data.get("datatables", []))
        custom_fields = self._get_custom_fields_details(import_def_data.get("fields", []))
        custom_artifact_types = self._get_custom_artifact_details(import_def_data.get("artifact_types", []))
        playbooks = self._get_playbook_details(import_def_data.get("playbooks", []))

        return functions, scripts, rules, datatables, custom_fields, custom_artifact_types, playbooks

    def _get_app_package_docgen_details(self, args, settings_file_contents={}):
        """
        Run docgen for an app package. Collect all the necessary elements of the
        app, including setup.py, export.res, screenshots, payload samples, etc...

        :param args: docgen command args obj
        :type args: argparse.Namespace
        :param settings_file_contents: json contents of settings file if provided
        :type settings_file_contents: dict, optional (default {})
        :raises SDKException: if path to package does not exist or setup.py file can't be read
        :return: display name, export.res contents, path to payload samples, path to save readme, requirements dict
        """
        # Get absolute path_to_src
        path_to_src = os.path.abspath(args.package)

        LOG.debug("Path to project: %s", path_to_src)

        # Generate path to setup.py file
        path_setup_py_file = os.path.join(path_to_src, package_helpers.BASE_NAME_SETUP_PY)

        try:
            # Ensure we have read permissions for setup.py
            sdk_helpers.validate_file_paths(os.R_OK, path_setup_py_file)
        except SDKException as err:
            err.message += "\nEnsure you are in the directory of the package you want to run docgen for"
            raise err

        # Parse the setup.py file
        setup_py_attributes = package_helpers.parse_setup_py(path_setup_py_file, package_helpers.SUPPORTED_SETUP_PY_ATTRIBUTE_NAMES)

        package_name = setup_py_attributes.get("name", "")

        # Generate paths to other required directories + files
        path_customize_py_file = os.path.join(path_to_src, package_name, package_helpers.PATH_CUSTOMIZE_PY)
        path_config_py_file = os.path.join(path_to_src, package_name, package_helpers.PATH_CONFIG_PY)
        path_readme = os.path.abspath(args.output) if args.output else os.path.join(path_to_src, package_helpers.BASE_NAME_README)
        path_screenshots_dir = os.path.join(path_to_src, package_helpers.PATH_SCREENSHOTS)
        path_payload_samples_dir = os.path.join(path_to_src, package_helpers.BASE_NAME_PAYLOAD_SAMPLES_DIR)

        # Ensure we have read permissions for each required file and the file exists
        sdk_helpers.validate_file_paths(os.R_OK, path_setup_py_file, path_customize_py_file, path_config_py_file)

        # Check doc/screenshots directory exists, if not, create it + copy default screenshot
        if not os.path.isdir(path_screenshots_dir):
            os.makedirs(path_screenshots_dir)
            shutil.copy(package_helpers.PATH_DEFAULT_SCREENSHOT, path_screenshots_dir)

        # Get the resilient_circuits dependency string from setup.py file
        res_circuits_dep_str = package_helpers.get_dependency_from_install_requires(
                setup_py_attributes.get(constants.SETUP_PY_INSTALL_REQ_NAME), constants.CIRCUITS_PACKAGE_NAME)

        # Get ImportDefinition from customize.py
        customize_py_import_def = package_helpers.get_import_definition_from_customize_py(path_customize_py_file)

        # Parse the app.configs from the config.py file
        jinja_app_configs = package_helpers.get_configs_from_config_py(path_config_py_file)

        # check if app is "supported" or not (based on settings.json or if ibm support URL in setup.py)
        supported_app = settings_file_contents.get("supported_app",
                            sdk_helpers.does_url_contain(setup_py_attributes.get("url", ""), "ibm.com/mysupport"))

        # If poller flag was given try to find the template details
        poller_templates = {}
        if args.poller:
            poller_templates = self._get_poller_details(path_to_src, package_name)

        requirements = {
            "setup_py_attributes": setup_py_attributes,
            "res_circuits_dep_str": res_circuits_dep_str,
            "jinja_app_configs": jinja_app_configs,
            "supported_app": supported_app,
            "poller_templates": poller_templates
        }

        return package_name, customize_py_import_def, path_payload_samples_dir, path_readme, requirements

    def _get_export_docgen_details(self, export_path, output_path=None):
        """
        Run docgen for an export file (export.res direct file or export.resz zip file).
        Return the output based on the --output param. If not given, default to README.md
        in the directory of the export_path

        :param export_path: must be a path to a .res or .resz file
        :type export_path: str
        :param output_path: output path to save the generated file
        :type output_path: str, optional (defaults to README.md in the same directory as the export file)
        :raises SDKException: if the given file path doesn't exist or we don't have permission to read it
        :return: display name of the intended export's readme,
                 export.res contents from the export,
                 absolute output path
        :rtype: tuple(str, dict, str)
        """
        # expand full path to export.res or playbook.resz (.zip) file
        path_export_file_or_zip = os.path.abspath(export_path)

        LOG.debug("Path to export file: %s", path_export_file_or_zip)

        # Ensure we have read permissions for to export
        try:
            sdk_helpers.validate_file_paths(os.R_OK, path_export_file_or_zip)
        except SDKException as err:
            err.message += "\nEnsure you give the path to a export.res or playbook.resz file"
            raise err

        # because we don't have another good option, we'll use the filename
        # without any extension for the display name
        display_name = os.path.basename(path_export_file_or_zip).split(".")[0]

        try:
            # try if export is direct .res file (JSON file)
            export_contents = package_helpers.get_import_definition_from_local_export_res(path_export_file_or_zip)
        except SDKException:
            # if not, it is a .resz (zip) file
            export_contents = package_helpers.get_export_from_zip(path_export_file_or_zip)

        # establish the path where we'll save the generated markdown file
        # this could be given by "-o"/"--output". if not given as a command-line
        # flag, we'll just put a README.md file in the same directory as the export file
        path_to_where_to_save_new_readme = os.path.abspath(output_path) if output_path else os.path.join(os.path.dirname(path_export_file_or_zip), package_helpers.BASE_NAME_README)

        # return the details
        return display_name, export_contents, path_to_where_to_save_new_readme

    def execute_command(self, args):
        LOG.debug("docgen called with %s", args)

        # Set docgen name for SDKException
        SDKException.command_ran = self.CMD_NAME

        # Validate that the given path to the sdk settings is valid
        try:
            sdk_helpers.validate_file_paths(os.R_OK, args.settings)
            # Parse the sdk_settings.json file
            settings_file_contents = sdk_helpers.read_json_file(args.settings, "docgen")
        except SDKException:
            args.settings = None
            settings_file_contents = {}
            LOG.debug("Given path to SDK Settings is either not valid or not readable. Ignoring and using built-in values for docgen")

        # branch off for export file vs standard package docgen by identifying appropriate function to call to get details
        if not args.export:
            package_name, export_contents, path_payload_samples_dir, path_readme, requirements_obj = self._get_app_package_docgen_details(args, settings_file_contents)
            jinja_functions, jinja_scripts, jinja_rules, jinja_datatables, jinja_custom_fields, jinja_custom_artifact_types, jinja_playbooks = self._get_all_objects_for_jinja_render(export_contents)
            self._add_payload_samples_to_functions(jinja_functions, path_payload_samples_dir)

            # Other variables for Jinja Templates
            server_version = export_contents.get("server_version", {}).get("version")
        else:
            package_names = []
            server_versions = []
            jinja_functions, jinja_scripts, jinja_rules, jinja_datatables, jinja_custom_fields, jinja_custom_artifact_types, jinja_playbooks = [], [], [], [], [], [], []
            requirements_obj = {"docgen_export": True}
            export_paths = self._get_export_paths_from_args(args.export)
            for export_path in export_paths:
                try:
                    package_name, export_contents, path_readme = self._get_export_docgen_details(export_path, args.output)
                except SDKException:
                    LOG.warning("File path '%s' was skipped for 'docgen --export' because it was not in the proper .res or .resz format", export_path)
                    continue

                functions, scripts, rules, datatables, custom_fields, custom_artifact_types, playbooks = self._get_all_objects_for_jinja_render(export_contents)
                jinja_functions.extend(functions)
                jinja_scripts.extend(scripts)
                jinja_rules.extend(rules)
                jinja_datatables.extend(datatables)
                jinja_custom_fields.extend(custom_fields)
                jinja_custom_artifact_types.extend(custom_artifact_types)
                jinja_playbooks.extend(playbooks)

                package_names.append(package_name)
                server_versions.append(export_contents.get("server_version", {}).get("version"))

            # collect package name and server version details from all exports
            package_name = ", ".join(package_names)
            server_version = max(server_versions)

        package_name_dash = package_name.replace("_", "-")

        # Instantiate Jinja2 Environment with path to Jinja2 templates
        jinja_env = sdk_helpers.setup_jinja_env("data/docgen/templates")
        readme_template = jinja_env.get_template(README_TEMPLATE_NAME)
        # Render the README Jinja2 Template with parameters
        LOG.info("Rendering README for %s", package_name_dash)
        rendered_readme = readme_template.render({
            # basic details
            "name_underscore": package_name,
            "name_dash": package_name_dash,
            "server_version": server_version,
            "display_name": requirements_obj.get("setup_py_attributes", {}).get("display_name", package_name),
            "short_description": requirements_obj.get("setup_py_attributes", {}).get("description"),
            "long_description": requirements_obj.get("setup_py_attributes", {}).get("long_description"),
            "version": requirements_obj.get("setup_py_attributes", {}).get("version"),
            "all_dependencies": requirements_obj.get("setup_py_attributes", {}).get("install_requires", []),
            "author": requirements_obj.get("setup_py_attributes", {}).get("author"),
            "support_url": requirements_obj.get("setup_py_attributes", {}).get("url"),
            "res_circuits_dependency_str": requirements_obj.get("res_circuits_dep_str"),
            "supported_app": requirements_obj.get("supported_app"),
            "app_configs": requirements_obj.get("jinja_app_configs", [{},{}])[1],

            # lists of customizations
            "functions": jinja_functions,
            "scripts": jinja_scripts,
            "rules": jinja_rules,
            "datatables": jinja_datatables,
            "custom_fields": jinja_custom_fields,
            "custom_artifact_types": jinja_custom_artifact_types,
            "playbooks": jinja_playbooks,

            # constants
            "placeholder_string": constants.DOCGEN_PLACEHOLDER_STRING,
            "poller_flag": args.poller,
            "poller_templates": requirements_obj.get("poller_templates", {}),
            "sdk_version": sdk_helpers.get_resilient_sdk_version(),
            "docgen_export": requirements_obj.get("docgen_export", False)
        })

        # Create a backup if needed of README
        sdk_helpers.rename_to_bak_file(path_readme, package_helpers.PATH_DEFAULT_README)

        # Write the new README
        LOG.info("Writing README to: %s", path_readme)
        sdk_helpers.write_file(path_readme, rendered_readme)
