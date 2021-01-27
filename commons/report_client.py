# -*- coding: utf-8 -*-
# !/usr/bin/python
#
# Copyright (c) 2020 Seagate Technology LLC and/or its Affiliates
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#
# For any questions about this software or licensing,
# please email opensource@seagate.com or cortx-questions@seagate.com.
#
""" Report Server client to update test results to Mongo DB"""
import threading
import requests
from commons import errorcodes
from commons.exceptions import CTException
from commons.utils import web_utils

REPORT_SRV = "http://cftic2.pun.seagate.com:5000/"  # todo discover report server
REPORT_SRV_CREATE = REPORT_SRV + "reportsdb/create"
REPORT_SRV_UPDATE = REPORT_SRV + "reportsdb/update"


class SingletonMixin:

    """ Singleton helper """
    __instance_lock = threading.Lock()
    __instance = None

    @classmethod
    def get_instance(cls):
        """class method to get an derived class instance"""
        if not cls.__instance:
            raise CTException(errorcodes.CT_SINGLETON_NOT_INITIALIZED)
        return cls.__instance

    @classmethod
    def init_instance(cls, *args, **kwargs):
        """Init class instance"""
        if cls.__instance:
            return
        with cls.__instance_lock:
            if cls.__instance:
                return
            else:
                cls.__instance = cls(*args, **kwargs)

    @classmethod
    def reinit_instance(cls, *args, **kwargs):
        """Only use when created instance is lost"""
        with cls.__instance_lock:
            if not cls.__instance:
                raise CTException(errorcodes.CT_SINGLETON_NOT_INITIALIZED)
            else:
                cls.__instance = cls(*args, **kwargs)

    @classmethod
    def clear_instance(cls):
        """Clean up method"""
        with cls.__instance_lock:
            if not cls.__instance:
                raise CTException(errorcodes.CT_SINGLETON_NOT_INITIALIZED)
            else:
                cls.__instance = None


class ReportClient(SingletonMixin):
    """Singleton Report client"""

    def __init__(self):
        self.session = requests.session()

    def create_db_entry(self, **data_kwargs):
        """
        Create a DB entry corresponding to a passing test case in execution.
        Updates all required fields and set default in case missing.
        This function has to be used in pytest reporting hook.
        :param data_kwargs: All parameters needed to update in DB
        :return: Response status.
        """
        payload = {"OSVersion": data_kwargs.get('os', "CentOS"),
                   "buildNo": data_kwargs.get('build', "0003"),
                   "buildType": data_kwargs.get('build_type', "Release"),
                   "clientHostname": data_kwargs.get('client_hostname', "autoclient"),
                   "executionType": data_kwargs.get('execution_type', "Automated"),
                   "healthCheckResult": data_kwargs.get('health_chk_res', "Pass"),
                   "isRegression": data_kwargs.get('is_regression', False),
                   "issueID": data_kwargs.get('issue_id', "EOS-000"),
                   "issueType": data_kwargs.get('issue_type', "Dev"),
                   "logCollectionDone": data_kwargs.get('are_logs_collected', True),
                   "logPath": data_kwargs.get('log_path', "DemoPath"),
                   "noOfNodes": data_kwargs.get('nodes', 4),
                   "nodesHostname": data_kwargs.get('nodes_hostnames', [
                       "sm7-r18.pun.seagate.com",
                       "sm8-r18.pun.seagate.com"
                   ]),
                   "testComponent": data_kwargs.get('test_component', "S3"),
                   "testExecutionID": data_kwargs.get('test_exec_id', "TEST-0000"),
                   "testExecutionTime": data_kwargs.get('test_exec_time', 0),
                   "testID": data_kwargs.get('test_exec_time', "TEST-0000"),
                   "testIDLabels": data_kwargs.get('test_exec_time', [
                       "Demo",
                       "Labels"
                   ]),
                   "testName": data_kwargs.get('test_name', "Demo test"),
                   "testPlanID": data_kwargs.get('test_plan_id', "TEST-0000"),
                   "testResult": data_kwargs.get('test_result', "Pass"),
                   "testStartTime": data_kwargs.get('start_time', "2021-01-02T09:01:38+00:00"),
                   "testTags": data_kwargs.get('tags', [
                       "Demo",
                       "Tags"
                   ]),
                   "testTeam": data_kwargs.get('test_team', "CFT"),
                   "testType": data_kwargs.get('test_type', "Pytest"),
                   "db_username": data_kwargs.get("db_username"),
                   "db_password": data_kwargs.get("db_password")
                   }

        headers = {
            'Content-Type': 'application/json'
        }
        response = web_utils.http_post_request(REPORT_SRV_CREATE, payload, headers, verify=False)
        print(response.text.encode('utf8'))
        return response.status_code

    def update_db_entry(self, **data_kwargs):
        """
        Update reports db entry at the end of execution
        :param data_kwargs:
        :return:
        """
        new_build_type = data_kwargs.get('update_build_type')
        payload = {"filter": {"buildType": data_kwargs.get('build_type', "Release")},
                   "update": {"$set": {"buildType": new_build_type,
                                       "OSVersion": data_kwargs.get('os')}},
                   "db_username": "",
                   "db_password": ""
                   }
        headers = {
            'Content-Type': 'application/json'
        }
        response = web_utils.http_patch_request(REPORT_SRV_UPDATE, payload, headers, verify=False)
        print(response.text.encode('utf8'))
        return response.status_code


def init_report_client():
    ReportClient.init_instance(init_params=None)


def report_client(server):
    return ReportClient.get_instance()