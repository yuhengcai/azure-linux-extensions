#!/usr/bin/env python
#
#CustomScript extension
#
# Copyright 2014 Microsoft Corporation
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#
# Requires Python 2.4+
#


import array
import base64
import os
import os.path
import re
import string
import subprocess
import sys
import imp
import shlex
import traceback
import urllib
from azure.storage import BlobService

waagent=imp.load_source('waagent','/usr/sbin/waagent')
from waagent import LoggerInit

LoggerInit('/var/log/waagent.log','/dev/stdout')
#Global Variables definition
DownloadDirectory = 'download'
ExtensionShortName = 'CustomScript'
Util=imp.load_source('HandlerUtil','./resources/HandlerUtil.py')

waagent.Log("%s started to handle." %(ExtensionShortName)) 

def main():
    for a in sys.argv[1:]:        
        if re.match("^([-/]*)(disable)", a):
            disable()
        elif re.match("^([-/]*)(uninstall)", a):
            uninstall()
        elif re.match("^([-/]*)(install)", a):
            install()
        elif re.match("^([-/]*)(enable)", a):
            enable()
        elif re.match("^([-/]*)(update)", a):
            update()

def install():
    hutil = Util.HandlerUtility(waagent.Log, waagent.Error, ExtensionShortName)
    hutil.do_parse_context('Install')
    hutil.do_exit(0,'Install','Installed','0', 'Install Succeeded')

def enable():
    hutil = Util.HandlerUtility(waagent.Log, waagent.Error, ExtensionShortName)
    try:
        hutil.do_parse_context('Enable')
        # Ensure the same configuration is executed only once
        # If the previous enable failed, we do not have retry logic here. Since the custom script may not work in an intermediate state
        hutil.exit_if_enabled()
        protected_settings = hutil._context._config['runtimeSettings'][0]['handlerSettings'].get('protectedSettings')
        public_settings = hutil._context._config['runtimeSettings'][0]['handlerSettings'].get('publicSettings')
        #get script in storage blob
        blob_uris = public_settings.get('fileUris')
        cmd = public_settings.get('commandToExecute')
        if blob_uris and len(blob_uris) > 0:
            hutil.do_status_report('Downloading','transitioning', '0', 'Downloading files...')
            if protected_settings:
                storage_account_name = protected_settings.get("storageAccountName")
                storage_account_key = protected_settings.get("storageAccountKey")
                if (not storage_account_name and storage_account_key) or (storage_account_name and not storage_account_key):
                    error_msg = "Azure storage account or storage key is not provided"
                    hutil.error(error_msg)
                    raise ValueError(error_msg)
                elif storage_account_name:
                    hutil.log("Downloading scripts from azure storage...")
                    for blob_uri in blob_uris:
                        download_blob(storage_account_name, storage_account_key, blob_uri, hutil._context._seq_no, cmd)
                else:       # neither storage account name no key specified in protected settings
                    hutil.log("No azure storage account and key specified in protected settings. Downloading scripts from external linsks...")
                    download_external_files(blob_uris, hutil._context._seq_no, cmd)
            else:
                hutil.log("Downloading scripts from external links...")
                download_external_files(blob_uris, hutil._context._seq_no, cmd)
        #execute the script
        hutil.log("Command to execute:" + cmd)
        args = shlex.split(cmd)
        hutil.do_status_report('Executing', 'transitioning', '0', 'Executing commands...')
        download_dir = os.getcwd()
        if len(blob_uris) > 0:
            download_dir = get_download_directory(hutil._context._seq_no)
        p = subprocess.Popen(args, cwd=download_dir, stdout=subprocess.PIPE)
        out,err = p.communicate()
        hutil.log('The custom script is executed with the output %s and error(if applied) %s.' %(out,err))
        #report the status
        hutil.do_exit(0, 'Enable', 'success','0', 'Enable Succeeded.')
    except Exception, e:
        hutil.error("Failed to enable the extension with error: %s, stack trace: %s" %(str(e), traceback.format_exc()))
        hutil.do_exit(1, 'Enable','error','0', 'Enable failed.')

def uninstall():
    hutil = Util.HandlerUtility(waagent.Log, waagent.Error, ExtensionShortName)
    hutil.do_parse_context('Uninstall')
    hutil.do_exit(0,'Uninstall','success','0', 'Uninstall succeeded')

def disable():
    hutil = Util.HandlerUtility(waagent.Log, waagent.Error, ExtensionShortName)
    hutil.do_parse_context('Disable')
    hutil.do_exit(0,'Disable','success','0', 'Disable Succeeded')

def update():
    hutil = Util.HandlerUtility(waagent.Log, waagent.Error, ExtensionShortName)
    hutil.do_parse_context('Upadate')
    hutil.do_exit(0,'Update','success','0', 'Update Succeeded')


def get_blob_name_from_uri(uri):
    return get_properties_from_uri(uri)['blob_name']

def get_container_name_from_uri(uri):
    return get_properties_from_uri(uri)['container_name']

def get_properties_from_uri(uri):
    if uri.endswith('/'):
        uri = uri[:-1]
    blob_name = uri[uri.rfind('/')+1:]
    uri = uri[:uri.rfind('/')]
    container_name = uri[uri.rfind('/')+1:]
    return {'blob_name': blob_name, 'container_name': container_name}

def download_blob(storage_account_name, storage_account_key, blob_uri, seqNo, command):
    container_name = get_container_name_from_uri(blob_uri)
    blob_name = get_blob_name_from_uri(blob_uri)
    download_dir = get_download_directory(seqNo)
    download_path = os.path.join(download_dir, blob_name)
    # Guest agent already ensure the plugin is enabled one after another. The blob download will not conflict.
    blob_service = BlobService(storage_account_name, storage_account_key)
    try:
        blob_service.get_blob_to_path(container_name, blob_name, download_path)
    except Exception, e:
        hutil.error("Failed to download blob with uri:" + blob_uri + "with error:" + str(e))
        raise
    if blob_name in command:
        os.chmod(download_path, 0100)

def download_external_files(uris, seqNo,command):
    for uri in uris:
        download_external_file(uri, seqNo, command)

def download_external_file(uri, seqNo, command):
    download_dir = get_download_directory(seqNo)
    file_name = uri.split('/')[-1]
    file_path = os.path.join(download_dir, file_name)
    try:
        urllib.urlretrieve(uri, file_path)
    except Exception, e:
        hutil.error("Failed to download external file with uri:" + blob_uri + "with error:" + str(e))
        raise
    if file_name in command:
        os.chmod(file_path, 0100)


def get_download_directory(seqNo):
    download_dir_main = os.path.join(os.getcwd(), 'download')
    create_directory_if_not_exists(download_dir_main)
    download_dir = os.path.join(download_dir_main, seqNo)
    create_directory_if_not_exists(download_dir)
    return download_dir

def create_directory_if_not_exists(directory):
    """create directory if no exists"""
    if not os.path.exists(directory):
        os.makedirs(directory)

if __name__ == '__main__' :
    main()
