import os
import sys

import pytest
from mock import patch
from resilient_sdk.cmds import CmdRunInit, base_cmd
from resilient_sdk.util import constants, sdk_validate_configs
from resilient_sdk.util.sdk_validate_issue import SDKValidateIssue
from tests.shared_mock_data import mock_paths
import json


''' fixtures that will be useful:
* fx_mk_temp_dir -- create a temporary directory at mock_paths.TEST_TEMP_DIR
* with patch('resilient_sdk.util.constants.SDK_SETTINGS_FILE_PATH') as mock_paths.MOCK_SDK_SETTINGS_PATH:
'''

def test_cmd_init_setup(fx_get_sub_parser, fx_cmd_line_args_init):
    cmd_init = CmdRunInit(fx_get_sub_parser)

    assert isinstance(cmd_init, base_cmd.BaseCmd)
    assert cmd_init.CMD_NAME == "init"
    assert cmd_init.CMD_HELP == "Generates sdk_settings.json to store default settings."
    assert cmd_init.CMD_USAGE == """
    $ resilient-sdk init
    $ resilient-sdk init -f/--file <path to settings json>
    $ resilient-sdk init -f/--file <path to settings json> -a/--author you@example.com
    """
    assert cmd_init.CMD_DESCRIPTION == cmd_init.CMD_HELP

def test_file_creation(fx_mk_temp_dir, fx_get_sub_parser, fx_cmd_line_args_init, fx_mock_settings_file_path):
    cmd_init = CmdRunInit(fx_get_sub_parser)
    args = cmd_init.parser.parse_known_args()[0]
    cmd_init.execute_command(args)
    assert os.path.exists(constants.SDK_SETTINGS_FILE_PATH)

def test_default_settings(fx_mk_temp_dir, fx_get_sub_parser, fx_cmd_line_args_init, fx_mock_settings_file_path):
    cmd_init = CmdRunInit(fx_get_sub_parser)
    args = cmd_init.parser.parse_known_args()[0]
    cmd_init.execute_command(args)
    with open(constants.SDK_SETTINGS_FILE_PATH) as f:
        settings_json = json.load(f)
        assert settings_json.get('codegen').get('setup').get('author') == constants.CODEGEN_DEFAULT_SETUP_PY_AUTHOR
        assert settings_json.get('codegen').get('setup').get('author_email') == constants.CODEGEN_DEFAULT_SETUP_PY_EMAIL
        assert settings_json.get('codegen').get('setup').get('url') == constants.CODEGEN_DEFAULT_SETUP_PY_URL
        assert settings_json.get('codegen').get('setup').get('license') == constants.CODEGEN_DEFAULT_SETUP_PY_LICENSE
        assert settings_json.get('codegen').get('license_content') == constants.CODEGEN_DEFAULT_LICENSE_CONTENT
        assert settings_json.get('docgen').get('supported_app') == False
        

def test_custom_settings_path(fx_mk_temp_dir, fx_get_sub_parser, fx_cmd_line_args_init):
    cmd_init = CmdRunInit(fx_get_sub_parser)
    my_new_path = "{}/my_test.json".format(mock_paths.TEST_TEMP_DIR)
    sys.argv.extend(["--file", my_new_path])
    args = cmd_init.parser.parse_known_args()[0]
    cmd_init.execute_command(args)
    assert os.path.exists(my_new_path)

def test_custom_args(fx_mk_temp_dir, fx_get_sub_parser, fx_cmd_line_args_init, fx_mock_settings_file_path):
    cmd_init = CmdRunInit(fx_get_sub_parser)
    sys.argv.extend(["-a", "test author", "-ae", "test@example.com", "-u", "hello.com", "-l", "My License"])
    args = cmd_init.parser.parse_known_args()[0]
    cmd_init.execute_command(args)
    with open(constants.SDK_SETTINGS_FILE_PATH) as f:
        settings_json = json.load(f)
        assert settings_json.get('codegen').get('setup').get('author') == "test author"
        assert settings_json.get('codegen').get('setup').get('author_email') == "test@example.com"
        assert settings_json.get('codegen').get('setup').get('url') == "hello.com"
        assert settings_json.get('codegen').get('setup').get('license') == "My License"

def test_internal_use(fx_mk_temp_dir, fx_get_sub_parser, fx_cmd_line_args_init, fx_mock_settings_file_path):
    cmd_init = CmdRunInit(fx_get_sub_parser)
    sys.argv.extend(["-i"])
    args = cmd_init.parser.parse_known_args()[0]
    cmd_init.execute_command(args)
    with open(constants.SDK_SETTINGS_FILE_PATH) as f:
        settings_json = json.load(f)
        assert settings_json.get('codegen').get('setup').get('author') == constants.INIT_INTERNAL_AUTHOR
        assert settings_json.get('codegen').get('setup').get('author_email') == constants.INIT_INTERNAL_AUTHOR_EMAIL
        assert settings_json.get('codegen').get('setup').get('url') == constants.INIT_INTERNAL_URL
        assert settings_json.get('codegen').get('setup').get('license') == constants.INIT_INTERNAL_LICENSE
        assert "Copyright © IBM Corporation" in settings_json.get("codegen").get('license_content')
        assert settings_json.get('docgen').get('supported_app') == True
