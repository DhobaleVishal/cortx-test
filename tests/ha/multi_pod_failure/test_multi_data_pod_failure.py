#!/usr/bin/python # pylint: disable=too-many-lines
# -*- coding: utf-8 -*-
#
# Copyright (c) 2022 Seagate Technology LLC and/or its Affiliates
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published
# by the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU Affero General Public License for more details.
# You should have received a copy of the GNU Affero General Public License
# along with this program. If not, see <https://www.gnu.org/licenses/>.
#
# For any questions about this software or licensing,
# please email opensource@seagate.com or cortx-questions@seagate.com.
#

"""
HA test suite for Multiple (K) Data Pods Failure
"""

import copy
import logging
import os
import secrets
import threading
import time
from multiprocessing import Queue
from time import perf_counter_ns

import pytest

from commons import constants as const
from commons.helpers.health_helper import Health
from commons.helpers.pods_helper import LogicalNode
from commons.params import TEST_DATA_FOLDER
from commons.utils import assert_utils
from commons.utils import system_utils as sysutils
from config import CMN_CFG
from config import HA_CFG
from config.s3 import S3_CFG
from libs.di.di_mgmt_ops import ManagementOPs
from libs.ha.ha_common_libs_k8s import HAK8s
from libs.prov.prov_k8s_cortx_deploy import ProvDeployK8sCortxLib
from libs.s3.s3_blackbox_test_lib import JCloudClient
from libs.s3.s3_multipart_test_lib import S3MultipartTestLib
from libs.s3.s3_rest_cli_interface_lib import S3AccountOperations
from libs.s3.s3_test_lib import S3TestLib

# Global Constants
LOGGER = logging.getLogger(__name__)


# pylint: disable=too-many-instance-attributes
# pylint: disable=too-many-public-methods
class TestMultiDataPodFailure:
    """
    Test suite for Multiple (K) Data Pods Failure
    """

    @classmethod
    def setup_class(cls):
        """
        Setup operations for the test file.
        """
        LOGGER.info("STARTED: Setup Module operations.")
        cls.csm_user = CMN_CFG["csm"]["csm_admin_user"]["username"]
        cls.csm_passwd = CMN_CFG["csm"]["csm_admin_user"]["password"]
        cls.username = list()
        cls.password = list()
        cls.node_master_list = list()
        cls.hlth_master_list = list()
        cls.node_worker_list = list()
        cls.pod_name_list = list()
        cls.ha_obj = HAK8s()
        cls.deploy_lc_obj = ProvDeployK8sCortxLib()
        cls.s3_clean = cls.test_prefix = None
        cls.s3acc_name = cls.s3acc_email = cls.bucket_name = cls.object_name = None
        cls.restore_pod = cls.deployment_backup = cls.deployment_name = cls.restore_method = None
        cls.node_name = cls.deploy = cls.kvalue = cls.multipart_obj_path = None
        cls.pod_dict = dict()
        cls.mgnt_ops = ManagementOPs()
        cls.system_random = secrets.SystemRandom()

        for node in range(len(CMN_CFG["nodes"])):
            cls.host = CMN_CFG["nodes"][node]["hostname"]
            cls.username.append(CMN_CFG["nodes"][node]["username"])
            cls.password.append(CMN_CFG["nodes"][node]["password"])
            if CMN_CFG["nodes"][node]["node_type"] == "master":
                cls.node_master_list.append(LogicalNode(hostname=cls.host,
                                                        username=cls.username[node],
                                                        password=cls.password[node]))
                cls.hlth_master_list.append(Health(hostname=cls.host,
                                                   username=cls.username[node],
                                                   password=cls.password[node]))
            else:
                cls.node_worker_list.append(LogicalNode(hostname=cls.host,
                                                        username=cls.username[node],
                                                        password=cls.password[node]))

        cls.rest_obj = S3AccountOperations()
        cls.s3_mp_test_obj = S3MultipartTestLib(endpoint_url=S3_CFG["s3_url"])
        cls.test_file = "ha-mp_obj"
        cls.test_dir_path = os.path.join(TEST_DATA_FOLDER, "HATestMultipartUpload")

    def setup_method(self):
        """
        This function will be invoked prior to each test case.
        """
        LOGGER.info("STARTED: Setup Operations")
        self.deploy = False
        self.s3_clean = dict()
        LOGGER.info("Check the overall status of the cluster.")
        resp = self.ha_obj.check_cluster_status(self.node_master_list[0])
        if not resp[0]:
            resp = self.ha_obj.restart_cluster(self.node_master_list[0])
            assert_utils.assert_true(resp[0], resp[1])
        LOGGER.info("Cluster status is online.")
        LOGGER.info("Get the value of K for the given cluster.")
        resp = self.ha_obj.get_config_value(self.node_master_list[0])
        if resp[0]:
            self.kvalue = int(resp[1]['cluster']['storage_set'][0]['durability']['sns']['parity'])
        else:
            LOGGER.info("Failed to get parity value, will use 1.")
            self.kvalue = 1
        LOGGER.info("The cluster has %s parity pods", self.kvalue)
        self.s3acc_name = f"ha_s3acc_{int(perf_counter_ns())}"
        self.s3acc_email = f"{self.s3acc_name}@seagate.com"
        self.bucket_name = f"ha-mp-bkt-{int(perf_counter_ns())}"
        self.object_name = f"ha-mp-obj-{int(perf_counter_ns())}"
        self.restore_pod = self.restore_method = self.deployment_name = None
        self.deployment_backup = None
        if not os.path.exists(self.test_dir_path):
            sysutils.make_dirs(self.test_dir_path)
        self.multipart_obj_path = os.path.join(self.test_dir_path, self.test_file)
        LOGGER.info("Done: Setup operations.")

    def teardown_method(self):
        """
        This function will be invoked after each test function in the module.
        """
        LOGGER.info("STARTED: Teardown Operations.")
        if self.s3_clean:
            LOGGER.info("Cleanup: Cleaning created IAM users and buckets.")
            resp = self.ha_obj.delete_s3_acc_buckets_objects(self.s3_clean)
            assert_utils.assert_true(resp[0], resp[1])
        if self.restore_pod:
            for pod_name in self.pod_name_list:
                if len(self.pod_dict.get(pod_name)) == 2:
                    deployment_name = self.pod_dict.get(pod_name)[1]
                    deployment_backup = None
                else:
                    deployment_name = self.pod_dict.get(pod_name)[2]
                    deployment_backup = self.pod_dict.get(pod_name)[1]
                resp = self.ha_obj.restore_pod(pod_obj=self.node_master_list[0],
                                               restore_method=self.restore_method,
                                               restore_params={"deployment_name": deployment_name,
                                                               "deployment_backup":
                                                                   deployment_backup})
                LOGGER.debug("Response: %s", resp)
                assert_utils.assert_true(resp[0], "Failed to restore pod by "
                                                  f"{self.restore_method} way")
                LOGGER.info("Successfully restored pod %s by %s way",
                            pod_name, self.restore_method)
        if os.path.exists(self.test_dir_path):
            sysutils.remove_dirs(self.test_dir_path)
        # TODO: Will need DTM support for pod restart and recovery so need to redeploy
        if self.deploy:
            LOGGER.info("Cleanup: Destroying the cluster ")
            resp = self.deploy_lc_obj.destroy_setup(self.node_master_list[0],
                                                    self.node_worker_list,
                                                    const.K8S_SCRIPTS_PATH)
            assert_utils.assert_true(resp[0], resp[1])
            LOGGER.info("Cleanup: Cluster destroyed successfully")

            LOGGER.info("Cleanup: Setting prerequisite")
            self.deploy_lc_obj.execute_prereq_cortx(self.node_master_list[0],
                                                    const.K8S_SCRIPTS_PATH,
                                                    const.K8S_PRE_DISK)
            for node in self.node_worker_list:
                self.deploy_lc_obj.execute_prereq_cortx(node, const.K8S_SCRIPTS_PATH,
                                                        const.K8S_PRE_DISK)
            LOGGER.info("Cleanup: Prerequisite set successfully")

            LOGGER.info("Cleanup: Deploying the Cluster")
            resp_cls = self.deploy_lc_obj.deploy_cluster(self.node_master_list[0],
                                                         const.K8S_SCRIPTS_PATH)
            assert_utils.assert_true(resp_cls[0], resp_cls[1])
            LOGGER.info("Cleanup: Cluster deployment successfully")

        LOGGER.info("Cleanup: Check cluster status")
        resp = self.ha_obj.check_cluster_status(self.node_master_list[0])
        assert_utils.assert_true(resp[0], resp[1])
        LOGGER.info("Cleanup: Cluster status checked successfully")

        LOGGER.info("Done: Teardown completed.")

    @pytest.mark.ha
    @pytest.mark.lc
    @pytest.mark.tags("TEST-35772")
    def test_degraded_reads_kpods_failure(self):
        """
        Test to verify degraded READs after all K data pods are failed.
        """
        LOGGER.info("Started: Test to verify degraded READs after all K data pods are failed.")

        LOGGER.info("STEP 1: Perform WRITEs/READs/verify DI with variable object sizes.")
        users = self.mgnt_ops.create_account_users(nusers=1)
        self.test_prefix = 'test-35772'
        self.s3_clean = users
        resp = self.ha_obj.ha_s3_workload_operation(s3userinfo=list(users.values())[0],
                                                    log_prefix=self.test_prefix,
                                                    skipcleanup=True, nsamples=2, nclients=2)
        assert_utils.assert_true(resp[0], resp[1])
        LOGGER.info("Step 1: Performed WRITEs/READs/Verify with variable sizes objects.")

        LOGGER.info("Step 2: Shutdown random %s (K) data pods by deleting deployment and "
                    "verify cluster & remaining pods status", self.kvalue)
        resp = self.ha_obj.delete_kpod_with_shutdown_methods(
            master_node_obj=self.node_master_list[0], health_obj=self.hlth_master_list[0],
            down_method=const.RESTORE_DEPLOYMENT_K8S, kvalue=self.kvalue)
        # Assert if empty dictionary
        assert_utils.assert_true(resp[1], "Failed to shutdown/delete pod")
        for pod_name in resp[1].keys():
            pod_data = list()
            pod_data.append(resp[1][pod_name]['hostname'])  # hostname
            pod_data.append(resp[1][pod_name]['deployment_backup'])  # deployment_backup
            pod_data.append(resp[1][pod_name]['deployment_name'])  # deployment_name
            self.pod_name_list.append(pod_name)
            self.pod_dict[pod_name] = pod_data
        self.restore_pod = self.deploy = True
        self.restore_method = const.RESTORE_DEPLOYMENT_K8S
        assert_utils.assert_true(resp[0], "Cluster/Services status is not as expected")
        LOGGER.info("Step 2: Successfully shutdown %s (K) data pod %s. Verified cluster and "
                    "services states are as expected & remaining pods status is online.",
                    self.kvalue, self.pod_name_list)

        LOGGER.info("Step 3: Perform READs and verify DI on the written data")
        resp = self.ha_obj.ha_s3_workload_operation(s3userinfo=list(users.values())[0],
                                                    log_prefix=self.test_prefix, skipwrite=True,
                                                    skipcleanup=True, nsamples=2, nclients=2,
                                                    setup_s3bench=False)
        assert_utils.assert_true(resp[0], resp[1])
        LOGGER.info("Step 3: Performed READs and verified DI on the written data")

        LOGGER.info("Completed: Test to verify degraded READs after all K data pods are failed.")

    @pytest.mark.ha
    @pytest.mark.lc
    @pytest.mark.tags("TEST-35771")
    def test_degraded_reads_till_kpods_fail(self):
        """
        Test to verify degraded READs after each pod failure till K data pods fail.
        """
        LOGGER.info("Started: Test to verify degraded READs after each pod failure till K "
                    "data pods fail.")

        LOGGER.info("STEP 1: Perform WRITEs/READs/Verify with variable object sizes.")
        users = self.mgnt_ops.create_account_users(nusers=1)
        self.test_prefix = 'test-35771'
        self.s3_clean = users
        resp = self.ha_obj.ha_s3_workload_operation(s3userinfo=list(users.values())[0],
                                                    log_prefix=self.test_prefix,
                                                    skipcleanup=True, nsamples=2, nclients=2)
        assert_utils.assert_true(resp[0], resp[1])
        LOGGER.info("Step 1: Performed WRITEs/READs/Verify with variable sizes objects.")

        LOGGER.info("Step 2: Shutdown %s (K) data pods one by one and verify read/verify "
                    "after each pod down.", self.kvalue)
        for count in range(1, self.kvalue+1):
            resp = self.ha_obj.delete_kpod_with_shutdown_methods(
                master_node_obj=self.node_master_list[0], health_obj=self.hlth_master_list[0],
                down_method=const.RESTORE_DEPLOYMENT_K8S)
            assert_utils.assert_true(resp[1], "Failed to shutdown/delete pod")
            pod_name = list(resp[1].keys())[0]
            pod_data = list()
            pod_data.append(resp[1][pod_name]['hostname'])  # hostname
            pod_data.append(resp[1][pod_name]['deployment_backup'])  # deployment_backup
            pod_data.append(resp[1][pod_name]['deployment_name'])  # deployment_name
            self.pod_dict[pod_name] = pod_data
            self.pod_name_list.append(pod_name)
            self.restore_pod = self.deploy = True
            self.restore_method = const.RESTORE_DEPLOYMENT_K8S
            assert_utils.assert_true(resp[0], "Cluster/Services status is not as expected")
            LOGGER.info("Deleted %s data pod %s by deleting deployment (unsafe)", count, pod_name)

            LOGGER.info("Step 3: Perform READs and verify DI on the written data")
            resp = self.ha_obj.ha_s3_workload_operation(s3userinfo=list(users.values())[0],
                                                        log_prefix=self.test_prefix, skipwrite=True,
                                                        skipcleanup=True, nsamples=2, nclients=2,
                                                        setup_s3bench=False)
            assert_utils.assert_true(resp[0], resp[1])
            LOGGER.info("Step 3: Performed READs and verified DI on the written data")
        LOGGER.info("Step 2: %s (K) %s data pods shutdown one by one successfully and read/verify "
                    "after each pod down verified", self.kvalue, self.pod_name_list)

        LOGGER.info("Completed: Test to verify degraded READs after each pod failure till K "
                    "data pods fail.")

    # pylint: disable=too-many-statements
    @pytest.mark.ha
    @pytest.mark.lc
    @pytest.mark.tags("TEST-35789")
    def test_k_rc_pod_fail(self):
        """
        Test to Verify degraded IOs after RC pod is taken down in loop till K pod failures.
        """
        LOGGER.info("Started: Test to Verify degraded IOs after RC pod is taken down in loop "
                    "till K pod failures.")

        LOGGER.info("STEP 1: Perform WRITE/READ/Verify/DELETEs with variable object sizes.")
        users = self.mgnt_ops.create_account_users(nusers=1)
        self.test_prefix = 'test-35789'
        self.s3_clean = users
        resp = self.ha_obj.ha_s3_workload_operation(s3userinfo=list(users.values())[0],
                                                    log_prefix=self.test_prefix)
        assert_utils.assert_true(resp[0], resp[1])
        LOGGER.info("Step 1: Performed WRITE/READ/Verify/DELETEs with variable sizes objects.")

        LOGGER.info("Shutdown RC node pod in loop and check IOs.")
        count = 1
        while self.kvalue > 0:
            LOGGER.info("Step 2: Get the RC node pod details.")
            pod_list = self.node_master_list[0].get_all_pods(pod_prefix=const.POD_NAME_PREFIX)
            rc_node = self.ha_obj.get_rc_node(self.node_master_list[0])
            rc_info = \
                self.node_master_list[0].get_pods_node_fqdn(pod_prefix=rc_node.split("svc-")[1])
            self.node_name = list(rc_info.values())[0]
            LOGGER.info("RC Node is running on %s node", self.node_name)
            LOGGER.info("Get the data pod running on %s node", self.node_name)
            data_pods = self.node_master_list[0].get_pods_node_fqdn(const.POD_NAME_PREFIX)
            rc_datapod = None
            for pod_name, node in data_pods.items():
                if node == self.node_name:
                    rc_datapod = pod_name
                    break
            self.pod_name_list.append(rc_datapod)
            LOGGER.info("Step 2: RC node %s has data pod: %s ", self.node_name, rc_datapod)

            LOGGER.info("Step 3: Shutdown %s data pod %s by deleting deployment (unsafe)",
                        count, rc_datapod)
            pod_data = list()
            pod_data.append(self.node_master_list[0].get_pod_hostname
                            (pod_name=rc_datapod))  # hostname
            resp = self.node_master_list[0].delete_deployment(pod_name=rc_datapod)
            LOGGER.debug("Response: %s", resp)
            assert_utils.assert_false(resp[0], f"Failed to delete {count} pod {rc_datapod} by "
                                               f"deleting deployment (unsafe)")
            pod_data.append(resp[1])  # deployment_backup
            pod_data.append(resp[2])  # deployment_name
            self.restore_pod = self.deploy = True
            self.restore_method = const.RESTORE_DEPLOYMENT_K8S
            self.pod_dict[rc_datapod] = pod_data
            LOGGER.info("Step 3: Deleted %s pod %s by deleting deployment (unsafe)",
                        count, rc_datapod)

            LOGGER.info("Step 4: Check cluster status")
            resp = self.ha_obj.check_cluster_status(self.node_master_list[0])
            assert_utils.assert_false(resp[0], resp)
            LOGGER.info("Step 4: Cluster is in degraded state")

            LOGGER.info("Step 5: Check services status that were running on pods which are deleted")
            hostname = self.pod_dict.get(rc_datapod)[0]
            resp = self.hlth_master_list[0].get_pod_svc_status(pod_list=[rc_datapod], fail=True,
                                                               hostname=hostname)
            assert_utils.assert_true(resp[0], resp[1])
            pod_list.remove(rc_datapod)
            LOGGER.info("Step 5: Services of pods are in offline state")

            LOGGER.info("Step 6: Check services status on remaining pods %s", pod_list)
            resp = self.hlth_master_list[0].get_pod_svc_status(pod_list=pod_list, fail=False)
            LOGGER.debug("Response: %s", resp)
            assert_utils.assert_true(resp[0], resp)
            LOGGER.info("Step 6: Services of pods are in online state")

            LOGGER.info("STEP 7: Perform WRITE/READ/Verify/DELETEs with variable object sizes.")
            users = self.mgnt_ops.create_account_users(nusers=1)
            self.test_prefix = f'test-35789-{count}'
            self.s3_clean.update(users)
            resp = self.ha_obj.ha_s3_workload_operation(s3userinfo=list(users.values())[0],
                                                        log_prefix=self.test_prefix,
                                                        nsamples=2, nclients=2, setup_s3bench=False)
            assert_utils.assert_true(resp[0], resp[1])
            LOGGER.info("Step 7: Performed WRITE/READ/Verify/DELETEs with variable sizes objects.")
            count += 1
            self.kvalue -= 1
        LOGGER.info("Shutdown %s RC node pods in loop and ran IOs", (count - 1))

        LOGGER.info("Completed: Test to Verify degraded IOs after RC pod is taken down in loop "
                    "till K pod failures.")

    # pylint: disable=too-many-locals
    @pytest.mark.ha
    @pytest.mark.lc
    @pytest.mark.tags("TEST-35790")
    @pytest.mark.skip(reason="Buckets cruds won't be supported with DTM0")
    def test_ios_during_data_kpods_down_safe(self):
        """
        Test to verify continuous IOs while k data pods are failing one by one by scale replicas
        method
        """
        LOGGER.info("STARTED: Test to verify IOs during k data pods are failing one by one")
        event = threading.Event()  # Event to be used to send when server pods going down
        wr_bucket = self.kvalue * 5 + HA_CFG["s3_bucket_data"]["no_buckets_for_deg_deletes"]
        LOGGER.info("Step 1: Perform WRITEs with variable object sizes on %s buckets "
                    "for parallel DELETEs.", wr_bucket)
        wr_output = Queue()
        del_output = Queue()
        remaining_bkt = 10
        del_bucket = wr_bucket - remaining_bkt
        users = self.mgnt_ops.create_account_users(nusers=1)
        self.s3_clean.update(users)
        access_key = list(users.values())[0]['accesskey']
        secret_key = list(users.values())[0]['secretkey']
        test_prefix_del = 'test-delete-35790'
        s3_test_obj = S3TestLib(access_key=access_key, secret_key=secret_key,
                                endpoint_url=S3_CFG["s3_url"])
        LOGGER.info("Create %s buckets and put variable size objects for parallel DELETEs",
                    wr_bucket)
        args = {'test_prefix': test_prefix_del, 'test_dir_path': self.test_dir_path,
                'skipget': True, 'skipdel': True, 'bkts_to_wr': wr_bucket, 'output': wr_output}
        self.ha_obj.put_get_delete(event, s3_test_obj, **args)
        wr_resp = tuple()
        while len(wr_resp) != 3:
            wr_resp = wr_output.get(timeout=HA_CFG["common_params"]["60sec_delay"])
        buckets = s3_test_obj.bucket_list()[1]
        assert_utils.assert_equal(len(buckets), wr_bucket, f"Failed to create {wr_bucket} number "
                                                           f"of buckets. Created {len(buckets)} "
                                                           "number of buckets")
        s3_data = wr_resp[0]
        LOGGER.info("Step 1: Successfully performed WRITEs with variable object sizes on %s "
                    "buckets for parallel DELETEs.", wr_bucket)

        LOGGER.info("Step 2: Perform WRITEs with variable object sizes for parallel READs")
        test_prefix_read = 'test-read-35790'
        resp = self.ha_obj.ha_s3_workload_operation(s3userinfo=list(users.values())[0],
                                                    log_prefix=test_prefix_read, skipread=True,
                                                    skipcleanup=True, nclients=5, nsamples=5)
        assert_utils.assert_true(resp[0], resp[1])
        LOGGER.info("Step 2: Performed WRITEs with variable sizes objects for parallel READs.")

        LOGGER.info("Starting three independent background threads for READs, WRITEs & DELETEs.")
        LOGGER.info("Step 3: Start Continuous DELETEs in background on random %s buckets",
                    del_bucket)
        bucket_list = list(s3_data.keys())
        get_random_buck = self.system_random.sample(bucket_list, del_bucket)
        del_random_buck = get_random_buck.copy()
        args = {'test_prefix': test_prefix_del, 'test_dir_path': self.test_dir_path,
                'skipput': True, 'skipget': True, 'bkt_list': del_random_buck,
                'output': del_output, 'bkts_to_del': del_bucket}
        thread_del = threading.Thread(target=self.ha_obj.put_get_delete,
                                      args=(event, s3_test_obj,), kwargs=args)
        thread_del.daemon = True  # Daemonize thread
        thread_del.start()
        LOGGER.info("Step 3: Successfully started DELETEs in background for %s buckets",
                    del_bucket)

        LOGGER.info("Step 4: Perform WRITEs with variable object sizes in background")
        test_prefix_write = 'test-write-35790'
        output_wr = Queue()
        event_set_clr = [False]
        args = {'s3userinfo': list(users.values())[0], 'log_prefix': test_prefix_write,
                'nclients': 1, 'nsamples': 5, 'skipread': True, 'skipcleanup': True,
                'output': output_wr, 'event_set_clr': event_set_clr, 'setup_s3bench': False}
        thread_wri = threading.Thread(target=self.ha_obj.event_s3_operation, args=(event,),
                                      kwargs=args)
        thread_wri.daemon = True  # Daemonize thread
        thread_wri.start()
        LOGGER.info("Step 4: Successfully started WRITEs with variable sizes objects"
                    " in background")

        LOGGER.info("Waiting for %s seconds to perform some WRITEs",
                    HA_CFG["common_params"]["30sec_delay"])
        time.sleep(HA_CFG["common_params"]["30sec_delay"])
        LOGGER.info("Step 5: Perform READs and verify DI on the written data in background")
        output_rd = Queue()
        args = {'s3userinfo': list(users.values())[0], 'log_prefix': test_prefix_read,
                'nclients': 1, 'nsamples': 5, 'skipwrite': True, 'skipcleanup': True,
                'output': output_rd, "setup_s3bench": False, 'event_set_clr': event_set_clr}
        thread_rd = threading.Thread(target=self.ha_obj.event_s3_operation, args=(event,),
                                     kwargs=args)
        thread_rd.daemon = True  # Daemonize thread
        thread_rd.start()
        LOGGER.info("Step 5: Successfully started READs and verified DI on the written data in "
                    "background")
        LOGGER.info("Waiting for %s seconds to perform READs",
                    HA_CFG["common_params"]["30sec_delay"])
        time.sleep(HA_CFG["common_params"]["30sec_delay"])

        LOGGER.info("Step 6: Shutdown random %s (K) data pods by making replicas=0 and "
                    "verify cluster & remaining pods status", self.kvalue)
        resp = self.ha_obj.delete_kpod_with_shutdown_methods(
            master_node_obj=self.node_master_list[0], health_obj=self.hlth_master_list[0],
            kvalue=self.kvalue, event=event, event_set_clr=event_set_clr)
        # Assert if empty dictionary
        assert_utils.assert_true(resp[1], "Failed to shutdown/delete pod")
        for pod_name in resp[1].keys():
            pod_data = list()
            pod_data.append(resp[1][pod_name]['hostname'])  # hostname
            pod_data.append(resp[1][pod_name]['deployment_name'])  # deployment_name
            self.pod_name_list.append(pod_name)
            self.pod_dict[pod_name] = pod_data
        self.restore_pod = self.deploy = True
        self.restore_method = const.RESTORE_SCALE_REPLICAS
        assert_utils.assert_true(resp[0], "Cluster/Services status is not as expected")
        LOGGER.info("Step 6: Successfully shutdown %s (K) data pod %s. Verified cluster and "
                    "services states are as expected & remaining pods status is online.",
                    self.kvalue, self.pod_name_list)

        LOGGER.info("Step 7: Verify status for In-flight READs/WRITEs/DELETEs while "
                    "%s (K) data pods were going down.", self.kvalue)
        LOGGER.info("Waiting for background IOs thread to join")
        thread_wri.join()
        thread_rd.join()
        thread_del.join()
        LOGGER.info("Step 7.1: Verify status for In-flight DELETEs while %s (K) data pods were"
                    "going down", self.kvalue)
        del_resp = tuple()
        while len(del_resp) != 2:
            del_resp = del_output.get(timeout=HA_CFG["common_params"]["60sec_delay"])
        if not del_resp:
            assert_utils.assert_true(False, "Background process failed to do deletes")
        fail_del_bkt = del_resp[1]
        # TODO: Expecting Failures when data pods going down. Re-test once CORTX-28541 is Resolved
        # event_del_bkt = del_resp[0]
        # assert_utils.assert_true(len(event_del_bkt), "Expected DELETEs failures during data "
        #                                              f"pod down {event_del_bkt}")
        assert_utils.assert_false(len(fail_del_bkt), "Expected pass, buckets which failed in "
                                                     f"DELETEs: {fail_del_bkt}.")
        LOGGER.info("Step 7.1: Verified status for In-flight DELETEs while %s (K) data"
                    " pods were going down", self.kvalue)

        LOGGER.info("Step 7.2: Verify status for In-flight WRITEs while %s (K) data pods going "
                    "down should be failed/error.", self.kvalue)
        responses_wr = dict()
        while len(responses_wr) != 2:
            responses_wr = output_wr.get(timeout=HA_CFG["common_params"]["60sec_delay"])
        pass_logs = list(x[1] for x in responses_wr["pass_res"])
        LOGGER.debug("Pass logs list: %s", pass_logs)
        fail_logs = list(x[1] for x in responses_wr["fail_res"])
        LOGGER.debug("Fail logs list: %s", fail_logs)
        resp = self.ha_obj.check_s3bench_log(file_paths=pass_logs)
        assert_utils.assert_false(len(resp[1]), f"WRITEs logs which contain failures: {resp[1]}")
        resp = self.ha_obj.check_s3bench_log(file_paths=fail_logs, pass_logs=False)
        # TODO: Expecting Failures when data pods going down. Re-test once CORTX-28541 is Resolved
        assert_utils.assert_true(len(resp[1]) <= len(fail_logs),
                                 f"WRITEs logs which contain pass: {resp[1]}")
        LOGGER.info("Step 7.2: Verified status for In-flight WRITEs while %s (K) data pods "
                    "going down.", self.kvalue)

        LOGGER.info("Step 7.3: Verify status for In-flight READs/Verify DI while %s (K)"
                    " data pods going down should be failed/error.", self.kvalue)
        responses_rd = dict()
        while len(responses_rd) != 2:
            responses_rd = output_rd.get(timeout=HA_CFG["common_params"]["60sec_delay"])
        pass_logs = list(x[1] for x in responses_rd["pass_res"])
        LOGGER.debug("Pass logs list: %s", pass_logs)
        fail_logs = list(x[1] for x in responses_rd["fail_res"])
        LOGGER.debug("Fail logs list: %s", fail_logs)
        resp = self.ha_obj.check_s3bench_log(file_paths=pass_logs)
        assert_utils.assert_false(len(resp[1]), f"Reads logs which contain failures: {resp[1]}")
        resp = self.ha_obj.check_s3bench_log(file_paths=fail_logs, pass_logs=False)
        # TODO: Expecting Failures when data pods going down. Re-test once CORTX-28541 is Resolved
        assert_utils.assert_true(len(resp[1]) <= len(fail_logs),
                                 f"Reads logs which contain pass: {resp[1]}")
        LOGGER.info("Step 7.3: Verified status for In-flight READs/Verify DI while %s (K)"
                    " data pods going down.", self.kvalue)
        LOGGER.info("Step 7: Verified status for In-flight READs/WRITEs/DELETEs while %s (K)"
                    " data pods were going down.", self.kvalue)

        LOGGER.info("Step 8: Perform IOs with variable sizes objects.")
        if CMN_CFG["dtm0_disabled"]:
            LOGGER.info("Step 8: Perform WRITEs-READs-Verify with variable object sizes "
                        "on degraded cluster with new user")
            users = self.mgnt_ops.create_account_users(nusers=1)
            test_prefix_read = 'test-35790-1'
            self.s3_clean.update(users)
        resp = self.ha_obj.ha_s3_workload_operation(s3userinfo=list(users.values())[0],
                                                    log_prefix=test_prefix_read,
                                                    nsamples=2, nclients=2, setup_s3bench=False)
        assert_utils.assert_true(resp[0], resp[1])
        LOGGER.info("Step 8: Performed IOs with variable sizes objects.")

        LOGGER.info("ENDED: Test to verify continuous IOs while k data pods are failing one by one")

    @pytest.mark.ha
    @pytest.mark.lc
    @pytest.mark.tags("TEST-35793")
    def test_chunk_upload_during_data_kpods_down(self):
        """
        Test chunk upload during k data pods going down using delete deployment (using jclient)
        """
        LOGGER.info("STARTED: Test chunk upload during K data pods going down by delete "
                    "deployment (using jclient)")
        file_size = HA_CFG["5gb_mpu_data"]["file_size"]
        download_file = "test_chunk_upload" + "_download"
        download_path = os.path.join(self.test_dir_path, download_file)
        chunk_obj_path = os.path.join(self.test_dir_path, self.object_name)
        output = Queue()
        event = threading.Event()

        LOGGER.info("Step 1: Perform setup steps for jclient")
        jc_obj = JCloudClient()
        resp = self.ha_obj.setup_jclient(jc_obj)
        assert_utils.assert_true(resp, "Failed in setting up jclient")
        LOGGER.info("Step 1: Successfully setup jcloud/jclient on runner")

        LOGGER.info("Step 2: Create IAM user with name %s, bucket %s and start chunk upload in "
                    "background", self.s3acc_name, self.bucket_name)
        resp = self.rest_obj.create_s3_account(acc_name=self.s3acc_name,
                                               email_id=self.s3acc_email,
                                               passwd=S3_CFG["CliConfig"]["s3_account"]["password"])
        assert_utils.assert_true(resp[0], resp[1])
        access_key = resp[1]["access_key"]
        secret_key = resp[1]["secret_key"]
        self.s3_clean = {'s3_acc': {'accesskey': access_key, 'secretkey': secret_key,
                                    'user_name': self.s3acc_name}}
        args = {'s3_data': self.s3_clean, 'bucket_name': self.bucket_name,
                'file_size': file_size, 'chunk_obj_path': chunk_obj_path, 'output': output}

        thread = threading.Thread(target=self.ha_obj.create_bucket_chunk_upload, kwargs=args)
        thread.daemon = True  # Daemonize thread
        thread.start()

        LOGGER.info("Step 2: Successfully started chuck upload in background. Sleeping for %s "
                    "sec", HA_CFG["common_params"]["30sec_delay"])
        time.sleep(HA_CFG["common_params"]["30sec_delay"])

        LOGGER.info("Step 3: Shutdown random %s (K) data pods by deleting deployment and "
                    "verify cluster & remaining pods status", self.kvalue)
        resp = self.ha_obj.delete_kpod_with_shutdown_methods(
            master_node_obj=self.node_master_list[0], health_obj=self.hlth_master_list[0],
            kvalue=self.kvalue, event=event, down_method=const.RESTORE_DEPLOYMENT_K8S)
        # Assert if empty dictionary
        assert_utils.assert_true(resp[1], "Failed to shutdown/delete pod")
        for pod_name in resp[1].keys():
            pod_data = list()
            pod_data.append(resp[1][pod_name]['hostname'])  # hostname
            pod_data.append(resp[1][pod_name]['deployment_backup'])  # deployment_backup
            pod_data.append(resp[1][pod_name]['deployment_name'])  # deployment_name
            self.pod_name_list.append(pod_name)
            self.pod_dict[pod_name] = pod_data
        self.restore_pod = self.deploy = True
        self.restore_method = const.RESTORE_DEPLOYMENT_K8S
        assert_utils.assert_true(resp[0], "Cluster/Services status is not as expected")
        LOGGER.info("Step 3: Successfully shutdown %s (K) data pod %s. Verified cluster and "
                    "services states are as expected & remaining pods status is online.",
                    self.kvalue, self.pod_name_list)

        LOGGER.info("Step 4: Verifying response of background process")
        thread.join()
        while True:
            resp = output.get(timeout=HA_CFG["common_params"]["60sec_delay"])
            if isinstance(resp, bool):
                break
        if resp is None:
            assert_utils.assert_true(False, "Background process of chunk upload failed")
        LOGGER.info("Step 4: Successfully verified response of background process")

        if not resp:
            LOGGER.info("Step 5: Chunk upload failed in between, trying chunk upload again")
            self.ha_obj.create_bucket_chunk_upload(s3_data=self.s3_clean,
                                                   bucket_name=self.bucket_name,
                                                   file_size=file_size,
                                                   chunk_obj_path=chunk_obj_path,
                                                   output=output,
                                                   bkt_op=False)

            while True:
                resp = output.get(timeout=HA_CFG["common_params"]["60sec_delay"])
                if isinstance(resp, bool):
                    break

            if not resp or resp is None:
                assert_utils.assert_true(False, "Retried chunk upload failed")
            LOGGER.info("Step 5: Retried chunk upload completed successfully")

        LOGGER.info("Calculating checksum of file %s", chunk_obj_path)
        upload_checksum = self.ha_obj.cal_compare_checksum(file_list=[chunk_obj_path],
                                                           compare=False)[0]

        LOGGER.info("Step 6: Download object and verify checksum")
        resp = self.ha_obj.object_download_jclient(s3_data=self.s3_clean,
                                                   bucket_name=self.bucket_name,
                                                   object_name=self.object_name,
                                                   obj_download_path=download_path)
        LOGGER.info("Download object response: %s", resp)
        assert_utils.assert_true(resp[0], resp[1])
        download_checksum = self.ha_obj.cal_compare_checksum(file_list=[download_path],
                                                             compare=False)[0]
        assert_utils.assert_equal(upload_checksum, download_checksum,
                                  f"Failed to match checksum: {upload_checksum},"
                                  f" {download_checksum}")
        LOGGER.info("Step 6: Successfully downloaded object and verified checksum")

        LOGGER.info("ENDED: Test chunk upload during k data pods going down by delete "
                    "deployment (unsafe)")

    @pytest.mark.ha
    @pytest.mark.lc
    @pytest.mark.tags("TEST-35777")
    def test_reads_writes_during_kpods_down(self):
        """
        This test tests continuous READs/WRITEs while pods are failing till K data pods are failed
        """
        LOGGER.info("STARTED: Test to verify continuous READs/WRITEs while %s (K) data pods "
                    "were going down.", self.kvalue)

        event = threading.Event()  # Event to be used to send intimation of data pod deletion
        users = self.mgnt_ops.create_account_users(nusers=1)
        self.s3_clean = users

        LOGGER.info("Step 1: Perform READs/WRITEs with variable object sizes during "
                    "data pod down by delete deployment.")
        LOGGER.info("Step 1.1: Perform WRITEs with variable object sizes for parallel READs")
        test_prefix_read = 'test-read-35777'
        resp = self.ha_obj.ha_s3_workload_operation(s3userinfo=list(users.values())[0],
                                                    log_prefix=test_prefix_read, skipread=True,
                                                    skipcleanup=True, nclients=5, nsamples=5)
        assert_utils.assert_true(resp[0], resp[1])
        LOGGER.info("Step 1.1: Performed WRITEs with variable sizes objects for parallel READs.")

        LOGGER.info("Step 1.2: Start WRITEs with variable object sizes in background")
        test_prefix_write = 'test-write-35777'
        output_wr = Queue()
        event_set_clr = [False]
        args = {'s3userinfo': list(users.values())[0], 'log_prefix': test_prefix_write,
                'nclients': 1, 'nsamples': 5, 'skipread': True, 'skipcleanup': True,
                'output': output_wr, 'setup_s3bench': False, 'event_set_clr': event_set_clr}
        thread_wri = threading.Thread(target=self.ha_obj.event_s3_operation, args=(event,),
                                      kwargs=args)
        thread_wri.daemon = True  # Daemonize thread
        thread_wri.start()
        LOGGER.info("Step 1.2: Successfully started WRITEs with variable sizes objects"
                    " in background")

        LOGGER.info("Step 1.3: Start READs and verify DI on the written data in background")
        output_rd = Queue()
        args = {'s3userinfo': list(users.values())[0], 'log_prefix': test_prefix_read,
                'nclients': 1, 'nsamples': 5, 'skipwrite': True, 'skipcleanup': True,
                'output': output_rd, 'setup_s3bench': False, 'event_set_clr': event_set_clr}
        thread_rd = threading.Thread(target=self.ha_obj.event_s3_operation, args=(event,),
                                     kwargs=args)
        thread_rd.daemon = True  # Daemonize thread
        thread_rd.start()
        LOGGER.info("Step 1.3: Successfully started READs and verified DI on the written data in "
                    "background")
        LOGGER.info("Step 1: Successfully started READs & WRITES in background.")
        LOGGER.info("Sleep for %s sec for some IOs", HA_CFG["common_params"]["30sec_delay"])
        time.sleep(HA_CFG["common_params"]["30sec_delay"])

        LOGGER.info("Step 2: Shutdown random %s (K) data pods by deleting deployment and "
                    "verify cluster & remaining pods status", self.kvalue)
        resp = self.ha_obj.delete_kpod_with_shutdown_methods(
            master_node_obj=self.node_master_list[0], health_obj=self.hlth_master_list[0],
            kvalue=self.kvalue, event=event, down_method=const.RESTORE_DEPLOYMENT_K8S,
            event_set_clr=event_set_clr)
        # Assert if empty dictionary
        assert_utils.assert_true(resp[1], "Failed to shutdown/delete pod")
        for pod_name in resp[1].keys():
            pod_data = list()
            pod_data.append(resp[1][pod_name]['hostname'])  # hostname
            pod_data.append(resp[1][pod_name]['deployment_backup'])  # deployment_backup
            pod_data.append(resp[1][pod_name]['deployment_name'])  # deployment_name
            self.pod_name_list.append(pod_name)
            self.pod_dict[pod_name] = pod_data
        self.restore_pod = self.deploy = True
        self.restore_method = const.RESTORE_DEPLOYMENT_K8S
        assert_utils.assert_true(resp[0], "Cluster/Services status is not as expected")
        LOGGER.info("Step 2: Successfully shutdown %s (K) data pod %s. Verified cluster and "
                    "services states are as expected & remaining pods status is online.",
                    self.kvalue, self.pod_name_list)

        LOGGER.info("Joining background thread. Waiting for %s seconds to "
                    "collect the queue logs", HA_CFG["common_params"]["60sec_delay"])
        thread_rd.join()
        thread_wri.join()
        LOGGER.debug("Threads has joined")

        LOGGER.info("Step 3: Verify responses from WRITEs & READs/VerifyDI background processes")
        LOGGER.info("Step 3.1: Verify status for In-flight WRITEs while %s (K) data pods going "
                    "down should be failed/error.", self.kvalue)
        responses_wr = dict()
        while len(responses_wr) != 2:
            responses_wr = output_wr.get(timeout=HA_CFG["common_params"]["60sec_delay"])
        pass_logs = list(x[1] for x in responses_wr["pass_res"])
        LOGGER.debug("Pass logs list: %s", pass_logs)
        fail_logs = list(x[1] for x in responses_wr["fail_res"])
        LOGGER.debug("Fail logs list: %s", fail_logs)
        resp = self.ha_obj.check_s3bench_log(file_paths=pass_logs)
        assert_utils.assert_false(len(resp[1]), f"WRITEs logs which contain failures: {resp[1]}")
        resp = self.ha_obj.check_s3bench_log(file_paths=fail_logs, pass_logs=False)
        assert_utils.assert_true(len(resp[1]) <= len(fail_logs),
                                 f"WRITEs logs which contain pass: {resp[1]}")
        LOGGER.info("Step 3.1: Verified status for In-flight WRITEs while %s (K) data pods "
                    "going down.", self.kvalue)

        LOGGER.info("Step 3.2: Verify status for In-flight READs/Verify DI while %s"
                    " (K) data pods going down should be failed/error.", self.kvalue)
        responses_rd = dict()
        while len(responses_rd) != 2:
            responses_rd = output_rd.get(timeout=HA_CFG["common_params"]["60sec_delay"])
        pass_logs = list(x[1] for x in responses_rd["pass_res"])
        LOGGER.debug("Pass logs list: %s", pass_logs)
        fail_logs = list(x[1] for x in responses_rd["fail_res"])
        LOGGER.debug("Fail logs list: %s", fail_logs)
        resp = self.ha_obj.check_s3bench_log(file_paths=pass_logs)
        assert_utils.assert_false(len(resp[1]),
                                  f"READs/VerifyDI logs which contain failures: {resp[1]}")
        resp = self.ha_obj.check_s3bench_log(file_paths=fail_logs, pass_logs=False)
        # TODO: Expecting Failures when data pods going down. Re-test once CORTX-28541 is Resolved
        assert_utils.assert_true(len(resp[1]) <= len(fail_logs),
                                 f"READs/VerifyDI logs which contain pass: {resp[1]}")
        LOGGER.info("Step 3.2: Verified status for In-flight READs/VerifyDI while %s (K)"
                    " data pods going down.", self.kvalue)
        LOGGER.info("Step 3: Verified responses from WRITEs & READs/VerifyDI background processes")

        LOGGER.info("STEP 4: Perform IOs with variable object sizes on degraded cluster")
        if CMN_CFG["dtm0_disabled"]:
            users = self.mgnt_ops.create_account_users(nusers=1)
            test_prefix_write = 'test-35777-1'
            self.s3_clean.update(users)
        resp = self.ha_obj.ha_s3_workload_operation(s3userinfo=list(users.values())[0],
                                                    log_prefix=test_prefix_write,
                                                    skipcleanup=True, nsamples=2, nclients=2,
                                                    setup_s3bench=False)
        assert_utils.assert_true(resp[0], resp[1])
        LOGGER.info("Step 4: Performed IOs with variable sizes objects.")

        LOGGER.info("ENDED: Test to verify continuous READs/WRITEs while %s (K) data pods "
                    "were going down.", self.kvalue)

    @pytest.mark.ha
    @pytest.mark.lc
    @pytest.mark.tags("TEST-35779")
    @pytest.mark.skip(reason="Buckets cruds won't be supported with DTM0")
    def test_deletes_after_kpods_failure(self):
        """
        This test tests Degraded DELETEs after all K data pods are failed
        """
        LOGGER.info("STARTED: Test to verify DELETEs after %s (K) data pods down", self.kvalue)
        wr_bucket = HA_CFG["s3_bucket_data"]["no_buckets_for_deg_deletes"]
        del_bucket = wr_bucket - 10
        event = threading.Event()
        wr_output = Queue()
        del_output = Queue()
        LOGGER.info("Step 1: Create %s buckets & perform WRITEs with variable size objects.",
                    wr_bucket)
        LOGGER.info("Create IAM user with name %s", self.s3acc_name)
        resp = self.rest_obj.create_s3_account(acc_name=self.s3acc_name,
                                               email_id=self.s3acc_email,
                                               passwd=S3_CFG["CliConfig"]["s3_account"]["password"])
        assert_utils.assert_true(resp[0], resp[1])
        access_key = resp[1]["access_key"]
        secret_key = resp[1]["secret_key"]
        self.test_prefix = 'test-35779'
        self.s3_clean = {'s3_acc': {'accesskey': access_key, 'secretkey': secret_key,
                                    'user_name': self.s3acc_name}}
        s3_test_obj = S3TestLib(access_key=access_key, secret_key=secret_key,
                                endpoint_url=S3_CFG["s3_url"])
        LOGGER.info("Successfully created IAM user with name %s", self.s3acc_name)

        LOGGER.info("Create %s buckets and put variable size objects.", wr_bucket)
        args = {'test_prefix': self.test_prefix, 'test_dir_path': self.test_dir_path,
                'skipget': True, 'skipdel': True, 'bkts_to_wr': wr_bucket, 'output': wr_output}

        self.ha_obj.put_get_delete(event, s3_test_obj, **args)
        wr_resp = tuple()
        while len(wr_resp) != 3:
            wr_resp = wr_output.get(timeout=HA_CFG["common_params"]["60sec_delay"])
        s3_data = wr_resp[0]  # Contains s3 data for passed buckets
        buckets = s3_test_obj.bucket_list()[1]
        assert_utils.assert_equal(len(buckets), wr_bucket, f"Failed to create {wr_bucket} number "
                                                           f"of buckets. Created {len(buckets)} "
                                                           f"number of buckets")
        LOGGER.info("Step 1: Successfully created %s buckets & perform WRITEs with variable size "
                    "objects.", wr_bucket)

        LOGGER.info("Step 2: Shutdown random %s (K) data pods by deleting deployment and "
                    "verify cluster & remaining pods status", self.kvalue)
        resp = self.ha_obj.delete_kpod_with_shutdown_methods(
            master_node_obj=self.node_master_list[0], health_obj=self.hlth_master_list[0],
            down_method=const.RESTORE_DEPLOYMENT_K8S, kvalue=self.kvalue)
        # Assert if empty dictionary
        assert_utils.assert_true(resp[1], "Failed to shutdown/delete pod")
        for pod_name in resp[1].keys():
            pod_data = list()
            pod_data.append(resp[1][pod_name]['hostname'])  # hostname
            pod_data.append(resp[1][pod_name]['deployment_backup'])  # deployment_backup
            pod_data.append(resp[1][pod_name]['deployment_name'])  # deployment_name
            self.pod_name_list.append(pod_name)
            self.pod_dict[pod_name] = pod_data
        self.restore_pod = self.deploy = True
        self.restore_method = const.RESTORE_DEPLOYMENT_K8S
        assert_utils.assert_true(resp[0], "Cluster/Services status is not as expected")
        LOGGER.info("Step 2: Successfully shutdown %s (K) data pod %s. Verified cluster and "
                    "services states are as expected & remaining pods status is online.",
                    self.kvalue, self.pod_name_list)

        LOGGER.info("Step 3: Perform DELETEs on random %s buckets", del_bucket)
        args = {'test_prefix': self.test_prefix, 'test_dir_path': self.test_dir_path,
                'skipput': True, 'skipget': True, 'bkts_to_del': del_bucket, 'output': del_output}

        self.ha_obj.put_get_delete(event, s3_test_obj, **args)
        del_resp = tuple()
        while len(del_resp) != 2:
            del_resp = del_output.get(timeout=HA_CFG["common_params"]["60sec_delay"])
        remain_bkt = s3_test_obj.bucket_list()[1]
        assert_utils.assert_equal(len(remain_bkt), wr_bucket - del_bucket,
                                  f"Failed to delete {del_bucket} number of buckets from "
                                  f"{wr_bucket}. Remaining {len(remain_bkt)} number of buckets")
        LOGGER.info("Step 3: Successfully performed DELETEs on random %s buckets", del_bucket)

        LOGGER.info("Step 4: Perform READs on the remaining %s buckets.", remain_bkt)
        rd_output = Queue()
        new_s3data = dict()
        for bkt in remain_bkt:
            new_s3data[bkt] = s3_data[bkt]
        args = {'test_prefix': self.test_prefix, 'test_dir_path': self.test_dir_path,
                'skipput': True, 'skipdel': True, 's3_data': new_s3data, 'di_check': True,
                'output': rd_output}
        self.ha_obj.put_get_delete(event, s3_test_obj, **args)
        rd_resp = tuple()
        while len(rd_resp) != 4:
            rd_resp = rd_output.get(timeout=HA_CFG["common_params"]["60sec_delay"])
        event_bkt_get = rd_resp[0]
        fail_bkt_get = rd_resp[1]
        event_di_bkt = rd_resp[2]
        fail_di_bkt = rd_resp[3]

        # Above four lists are expected to be empty as all pass expected
        assert_utils.assert_false(len(fail_bkt_get) or len(fail_di_bkt) or len(event_bkt_get) or
                                  len(event_di_bkt), "Expected pass in read and di check "
                                                     "operations. Found failures in READ: "
                                                     f"{fail_bkt_get} {event_bkt_get}"
                                                     f"or DI_CHECK: {fail_di_bkt} {event_di_bkt}")
        LOGGER.info("Step 4: Successfully performed READs on the remaining %s buckets.", remain_bkt)

        LOGGER.info("ENDED: Test to verify DELETEs after %s (K) data pods down.", self.kvalue)

    @pytest.mark.ha
    @pytest.mark.lc
    @pytest.mark.tags("TEST-35773")
    def test_reads_during_kpods_down(self):
        """
        This test tests READs while pods are failing till K data pods are failed
        """
        LOGGER.info("STARTED: Test to verify READs during %s (K) data pods down", self.kvalue)
        LOGGER.info("Step 1: Perform WRITEs with variable object sizes.")
        users = self.mgnt_ops.create_account_users(nusers=1)
        self.test_prefix = 'test-35773'
        self.s3_clean.update(users)
        resp = self.ha_obj.ha_s3_workload_operation(s3userinfo=list(users.values())[0],
                                                    log_prefix=self.test_prefix, skipread=True,
                                                    skipcleanup=True, nclients=5, nsamples=5)
        assert_utils.assert_true(resp[0], resp[1])
        LOGGER.info("Step 1: Performed WRITEs with variable sizes objects.")

        LOGGER.info("Step 2: Perform READs/VerifyDI on written data in background")
        output = Queue()
        event = threading.Event()
        event_set_clr = [False]
        args = {'s3userinfo': list(users.values())[0], 'log_prefix': self.test_prefix,
                'nclients': 1, 'nsamples': 5, 'skipwrite': True, 'skipcleanup': True,
                'output': output, 'event_set_clr': event_set_clr, 'setup_s3bench': False}

        thread = threading.Thread(target=self.ha_obj.event_s3_operation, args=(event,), kwargs=args)
        thread.daemon = True  # Daemonize thread
        thread.start()
        LOGGER.info("Step 2: Successfully started READs/VerifyDI on written data in background")

        LOGGER.info("Step 3: Shutdown random %s (K) data pods by deleting deployment and "
                    "verify cluster & remaining pods status", self.kvalue)
        resp = self.ha_obj.delete_kpod_with_shutdown_methods(
            master_node_obj=self.node_master_list[0], health_obj=self.hlth_master_list[0],
            kvalue=self.kvalue, event=event, down_method=const.RESTORE_DEPLOYMENT_K8S,
            event_set_clr=event_set_clr)
        # Assert if empty dictionary
        assert_utils.assert_true(resp[1], "Failed to shutdown/delete pod")
        for pod_name in resp[1].keys():
            pod_data = list()
            pod_data.append(resp[1][pod_name]['hostname'])  # hostname
            pod_data.append(resp[1][pod_name]['deployment_backup'])  # deployment_backup
            pod_data.append(resp[1][pod_name]['deployment_name'])  # deployment_name
            self.pod_name_list.append(pod_name)
            self.pod_dict[pod_name] = pod_data
        self.restore_pod = self.deploy = True
        self.restore_method = const.RESTORE_DEPLOYMENT_K8S
        assert_utils.assert_true(resp[0], "Cluster/Services status is not as expected")
        LOGGER.info("Step 3: Successfully shutdown %s (K) data pod %s. Verified cluster and "
                    "services states are as expected & remaining pods status is online.",
                    self.kvalue, self.pod_name_list)

        LOGGER.info("Step 4: Verify status for In-flight READs/Verify DI while %s (K) data pods "
                    "going down should be failed/error.", self.kvalue)
        thread.join()
        responses = dict()
        while len(responses) != 2:
            responses = output.get(timeout=HA_CFG["common_params"]["60sec_delay"])
        pass_logs = list(x[1] for x in responses["pass_res"])
        fail_logs = list(x[1] for x in responses["fail_res"])
        resp = self.ha_obj.check_s3bench_log(file_paths=pass_logs)
        assert_utils.assert_false(len(resp[1]), f"Logs which contain failures: {resp[1]}")
        resp = self.ha_obj.check_s3bench_log(file_paths=fail_logs, pass_logs=False)
        # TODO: Expecting Failures when data pods going down. Re-test once CORTX-28541 is Resolved
        assert_utils.assert_true(len(resp[1]) <= len(fail_logs),
                                 f"Logs which contain pass: {resp[1]}")
        LOGGER.info("Step 4: Verified status for In-flight READs/Verify DI while %s (K) data pods "
                    "going down.", self.kvalue)

        LOGGER.info("STEP 5: Perform IOs with variable object sizes on degraded cluster")
        if CMN_CFG["dtm0_disabled"]:
            users = self.mgnt_ops.create_account_users(nusers=1)
            self.test_prefix = 'test-35773-1'
            self.s3_clean.update(users)
        resp = self.ha_obj.ha_s3_workload_operation(s3userinfo=list(users.values())[0],
                                                    log_prefix=self.test_prefix, skipcleanup=True,
                                                    nsamples=2, nclients=2, setup_s3bench=False)
        assert_utils.assert_true(resp[0], resp[1])
        LOGGER.info("Step 5: Performed IOs with variable sizes objects.")
        LOGGER.info("ENDED: Test to verify continuous READs during %s (K) data pods down",
                    self.kvalue)

    @pytest.mark.ha
    @pytest.mark.lc
    @pytest.mark.tags("TEST-35776")
    def test_writes_during_kpods_down(self):
        """
        This test tests WRITEs while pods are failing till K data pods are failed
        """
        LOGGER.info("STARTED: Test to verify WRITEs during %s (K) data pods down", self.kvalue)
        LOGGER.info("Step 1: Perform WRITEs with variable object sizes in background")
        users = self.mgnt_ops.create_account_users(nusers=1)
        self.test_prefix = 'test-35776'
        self.s3_clean.update(users)
        output = Queue()
        event = threading.Event()
        event_set_clr = [False]
        args = {'s3userinfo': list(users.values())[0], 'log_prefix': self.test_prefix,
                'nclients': 1, 'nsamples': 5, 'skipread': True, 'skipcleanup': True,
                'output': output, 'event_set_clr': event_set_clr}

        thread = threading.Thread(target=self.ha_obj.event_s3_operation,
                                  args=(event,), kwargs=args)
        thread.daemon = True  # Daemonize thread
        thread.start()
        LOGGER.info("Step 1: Successfully started WRITEs with variable sizes objects in background")

        LOGGER.info("Step 2: Shutdown random %s (K) data pods by deleting deployment and "
                    "verify cluster & remaining pods status", self.kvalue)
        resp = self.ha_obj.delete_kpod_with_shutdown_methods(
            master_node_obj=self.node_master_list[0], health_obj=self.hlth_master_list[0],
            kvalue=self.kvalue, event=event, down_method=const.RESTORE_DEPLOYMENT_K8S,
            event_set_clr=event_set_clr)
        # Assert if empty dictionary
        assert_utils.assert_true(resp[1], "Failed to shutdown/delete pod")
        for pod_name in resp[1].keys():
            pod_data = list()
            pod_data.append(resp[1][pod_name]['hostname'])  # hostname
            pod_data.append(resp[1][pod_name]['deployment_backup'])  # deployment_backup
            pod_data.append(resp[1][pod_name]['deployment_name'])  # deployment_name
            self.pod_name_list.append(pod_name)
            self.pod_dict[pod_name] = pod_data
        self.restore_pod = self.deploy = True
        self.restore_method = const.RESTORE_DEPLOYMENT_K8S
        assert_utils.assert_true(resp[0], "Cluster/Services status is not as expected")
        LOGGER.info("Step 2: Successfully shutdown %s (K) data pod %s. Verified cluster and "
                    "services states are as expected & remaining pods status is online.",
                    self.kvalue, self.pod_name_list)

        LOGGER.info("Step 3: Verify status for In-flight WRITEs while %s (K) data pods going "
                    "down should be failed/error.", self.kvalue)
        thread.join()
        responses = dict()
        while len(responses) != 2:
            responses = output.get(timeout=HA_CFG["common_params"]["60sec_delay"])
        pass_logs = list(x[1] for x in responses["pass_res"])
        fail_logs = list(x[1] for x in responses["fail_res"])
        resp = self.ha_obj.check_s3bench_log(file_paths=pass_logs)
        assert_utils.assert_false(len(resp[1]), f"Logs which contain failures: {resp[1]}")
        resp = self.ha_obj.check_s3bench_log(file_paths=fail_logs, pass_logs=False)
        # TODO: Expecting Failures when data pods going down. Re-test once CORTX-28541 is Resolved
        assert_utils.assert_true(len(resp[1]) <= len(fail_logs),
                                 f"Logs which contain pass: {resp[1]}")
        LOGGER.info("Step 3: Verified status for In-flight WRITEs while %s (K) data pods "
                    "going down.", self.kvalue)

        LOGGER.info("STEP 4: Perform IOs with variable object sizes on degraded cluster")
        if CMN_CFG["dtm0_disabled"]:
            users = self.mgnt_ops.create_account_users(nusers=1)
            self.test_prefix = 'test-35776-1'
            self.s3_clean.update(users)
        resp = self.ha_obj.ha_s3_workload_operation(s3userinfo=list(users.values())[0],
                                                    log_prefix=self.test_prefix, skipcleanup=True,
                                                    nsamples=2, nclients=2, setup_s3bench=False)
        assert_utils.assert_true(resp[0], resp[1])
        LOGGER.info("Step 4: Performed IOs with variable sizes objects.")

        LOGGER.info("ENDED: Test to verify WRITEs during %s (K) data pods down", self.kvalue)

    @pytest.mark.ha
    @pytest.mark.lc
    @pytest.mark.skip(reason="Buckets cruds won't be supported with DTM0")
    @pytest.mark.tags("TEST-35780")
    def test_deletes_during_kpods_down(self):
        """
        This test tests DELETEs while pods are failing till K data pods are failed
        """
        LOGGER.info("STARTED: Test to verify DELETEs while pods are failing till K "
                    "data pods are failed.")
        event = threading.Event()  # Event to be used to send intimation of pod deletion
        wr_output = Queue()
        del_output = Queue()
        wr_bucket = HA_CFG["s3_bucket_data"]["no_buckets_for_deg_deletes"]
        remaining_bkt = 10
        del_bucket = wr_bucket - remaining_bkt
        LOGGER.info("Step 1: Perform WRITEs with variable object sizes. (0B - 128MB) on %s "
                    "buckets", wr_bucket)
        LOGGER.info("Create IAM user with name %s", self.s3acc_name)
        resp = self.rest_obj.create_s3_account(acc_name=self.s3acc_name,
                                               email_id=self.s3acc_email,
                                               passwd=S3_CFG["CliConfig"]["s3_account"]["password"])
        assert_utils.assert_true(resp[0], resp[1])
        access_key = resp[1]["access_key"]
        secret_key = resp[1]["secret_key"]
        self.test_prefix = 'test-35780'
        self.s3_clean = {'s3_acc': {'accesskey': access_key, 'secretkey': secret_key,
                                    'user_name': self.s3acc_name}}
        s3_test_obj = S3TestLib(access_key=access_key, secret_key=secret_key,
                                endpoint_url=S3_CFG["s3_url"])
        LOGGER.info("Successfully created IAM user with name %s", self.s3acc_name)

        LOGGER.info("Create %s buckets and put variable size objects.", wr_bucket)
        args = {'test_prefix': self.test_prefix, 'test_dir_path': self.test_dir_path,
                'skipget': True, 'skipdel': True, 'bkts_to_wr': wr_bucket, 'output': wr_output}

        self.ha_obj.put_get_delete(event, s3_test_obj, **args)
        wr_resp = tuple()
        while len(wr_resp) != 3:
            wr_resp = wr_output.get(timeout=HA_CFG["common_params"]["60sec_delay"])
        s3_data_del = wr_resp[0]  # Contains IAM user data for passed buckets
        buckets = s3_test_obj.bucket_list()[1]
        assert_utils.assert_equal(len(buckets), wr_bucket, f"Failed to create {wr_bucket} number "
                                                           f"of buckets. Created {len(buckets)} "
                                                           "number of buckets")
        LOGGER.info("Step 1: Successfully performed WRITEs with variable object sizes. (0B - "
                    "128MB) on %s buckets", wr_bucket)
        LOGGER.info("Step 2: Start Continuous DELETEs in background on random %s buckets",
                    del_bucket)
        get_random_buck = self.system_random.sample(buckets, del_bucket)
        del_random_buck = get_random_buck.copy()
        args = {'test_prefix': self.test_prefix, 'test_dir_path': self.test_dir_path,
                'skipput': True, 'skipget': True, 'bkt_list': del_random_buck,
                'output': del_output, 'bkts_to_del': del_bucket}
        thread = threading.Thread(target=self.ha_obj.put_get_delete,
                                  args=(event, s3_test_obj,), kwargs=args)
        thread.daemon = True  # Daemonize thread
        thread.start()
        LOGGER.info("Step 2: Successfully started DELETEs in background for %s buckets", del_bucket)

        LOGGER.info("Step 3: Shutdown random %s (K) data pods by deleting deployment and "
                    "verify cluster & remaining pods status", self.kvalue)
        resp = self.ha_obj.delete_kpod_with_shutdown_methods(
            master_node_obj=self.node_master_list[0], health_obj=self.hlth_master_list[0],
            kvalue=self.kvalue, event=event, down_method=const.RESTORE_DEPLOYMENT_K8S)
        # Assert if empty dictionary
        assert_utils.assert_true(resp[1], "Failed to shutdown/delete pod")
        for pod_name in resp[1].keys():
            pod_data = list()
            pod_data.append(resp[1][pod_name]['hostname'])  # hostname
            pod_data.append(resp[1][pod_name]['deployment_backup'])  # deployment_backup
            pod_data.append(resp[1][pod_name]['deployment_name'])  # deployment_name
            self.pod_name_list.append(pod_name)
            self.pod_dict[pod_name] = pod_data
        self.restore_pod = self.deploy = True
        self.restore_method = const.RESTORE_DEPLOYMENT_K8S
        assert_utils.assert_true(resp[0], "Cluster/Services status is not as expected")
        LOGGER.info("Step 3: Successfully shutdown %s (K) data pod %s. Verified cluster and "
                    "services states are as expected & remaining pods status is online.",
                    self.kvalue, self.pod_name_list)

        LOGGER.info("Step 4: Verify status for In-flight DELETEs while %s (K) pods data were"
                    "going down", self.kvalue)
        LOGGER.info("Waiting for background DELETEs thread to join. Waiting for %s seconds to "
                    "collect the queue logs", HA_CFG["common_params"]["60sec_delay"])
        thread.join()
        del_resp = tuple()
        while len(del_resp) != 2:
            del_resp = del_output.get(timeout=HA_CFG["common_params"]["60sec_delay"])
        if not del_resp:
            assert_utils.assert_true(False, "Background process failed to do deletes")
        event_del_bkt = del_resp[0]
        fail_del_bkt = del_resp[1]
        assert_utils.assert_false(len(fail_del_bkt), "Expected pass, buckets which failed in "
                                                     f"DELETEs: {fail_del_bkt}.")
        # TODO: Expecting Failures when data pods going down. Re-test once CORTX-28541 is Resolved
        # assert_utils.assert_true(len(event_del_bkt), "No bucket DELETEs failed during "
        #                                              f"data pod down {event_del_bkt}")
        LOGGER.info("Failed buckets while in-flight DELETEs operation : %s", event_del_bkt)
        LOGGER.info("Step 4: Verified status for In-flight DELETEs while %s (K) data pods were"
                    "going down", self.kvalue)

        LOGGER.info("Step 5: Perform DELETEs on remaining FailedToDelete buckets when pods were "
                    "going down, on degraded cluster.")
        rem_bkts_after_del = list(set(buckets) - set(get_random_buck))
        new_s3data = dict()
        for bkt in rem_bkts_after_del:
            new_s3data[bkt] = s3_data_del[bkt]
        fail_del_op = Queue()
        args = {'test_prefix': self.test_prefix, 'test_dir_path': self.test_dir_path,
                'skipput': True, 'skipget': True, 'bkt_list': event_del_bkt, 'output': fail_del_op}
        self.ha_obj.put_get_delete(event, s3_test_obj, **args)
        del_resp = tuple()
        while len(del_resp) != 2:
            del_resp = fail_del_op.get(timeout=HA_CFG["common_params"]["60sec_delay"])
        event_del_bkt = del_resp[0]
        fail_del_bkt = del_resp[1]
        assert_utils.assert_false(len(event_del_bkt) or len(fail_del_bkt),
                                  f"Failed to delete buckets: either {event_del_bkt} or"
                                  f" {fail_del_bkt}")
        LOGGER.info("Step 5: Successfully performed DELETEs on remaining FailedToDelete buckets "
                    "when pods were going down, on degraded cluster.")

        LOGGER.info("Step 6: Verify read on the remaining %s buckets.", len(rem_bkts_after_del))
        rd_output = Queue()
        args = {'test_prefix': self.test_prefix, 'test_dir_path': self.test_dir_path,
                'skipput': True, 'skipdel': True, 'bkt_list': rem_bkts_after_del, 'di_check': True,
                'output': rd_output, 's3_data': new_s3data}
        self.ha_obj.put_get_delete(event, s3_test_obj, **args)
        rd_resp = tuple()
        while len(rd_resp) != 4:
            rd_resp = rd_output.get(timeout=HA_CFG["common_params"]["60sec_delay"])
        event_bkt_get = rd_resp[0]
        fail_bkt_get = rd_resp[1]
        event_di_bkt = rd_resp[2]
        fail_di_bkt = rd_resp[3]
        # Above four lists are expected to be empty as all pass expected
        assert_utils.assert_false(len(fail_bkt_get) or len(fail_di_bkt) or len(event_bkt_get) or
                                  len(event_di_bkt),
                                  "Expected pass in read and di check operations. Found failures "
                                  f"in READ: {fail_bkt_get} {event_bkt_get}"
                                  f"or DI_CHECK: {fail_di_bkt} {event_di_bkt}")
        LOGGER.info("Step 6: Successfully verified READs & DI check for remaining buckets")

        LOGGER.info("STEP 7: Create new user and perform WRITEs-READs-Verify-DELETEs with "
                    "variable object sizes on degraded cluster")
        users = self.mgnt_ops.create_account_users(nusers=1)
        self.test_prefix = 'test-35780-1'
        self.s3_clean.update(users)
        resp = self.ha_obj.ha_s3_workload_operation(s3userinfo=list(users.values())[0],
                                                    log_prefix=self.test_prefix, nsamples=2,
                                                    nclients=2)
        assert_utils.assert_true(resp[0], resp[1])
        LOGGER.info("Step 7: Performed WRITEs-READs-Verify-DELETEs with variable sizes objects.")

        LOGGER.info("ENDED: Test to verify DELETEs while pods are failing till K "
                    "data pods are failed.")

    # pylint: disable=C0321
    # pylint: disable=too-many-locals
    @pytest.mark.ha
    @pytest.mark.lc
    @pytest.mark.tags("TEST-35781")
    def test_ios_during_kpods_down(self):
        """
        This test tests continuous READs/WRITEs/DELETEs while pods are failing till K
        data pods are failed
        """
        LOGGER.info("STARTED: Test to verify continuous READs/WRITEs/DELETEs while pods are "
                    "failing till K data pods are failed.")

        event = threading.Event()  # Event to be used to send when data pods going down
        wr_bucket = HA_CFG["s3_bucket_data"]["no_buckets_for_deg_deletes"]
        LOGGER.info("Step 1: Perform WRITEs with variable object sizes on %s buckets "
                    "for parallel DELETEs.", wr_bucket)
        wr_output = Queue()
        del_output = Queue()
        remaining_bkt = 10
        del_bucket = wr_bucket - remaining_bkt
        users = self.mgnt_ops.create_account_users(nusers=1)
        self.s3_clean.update(users)
        access_key = list(users.values())[0]['accesskey']
        secret_key = list(users.values())[0]['secretkey']
        test_prefix_del = 'test-delete-35781'
        s3_test_obj = S3TestLib(access_key=access_key, secret_key=secret_key,
                                endpoint_url=S3_CFG["s3_url"])
        LOGGER.info("Create %s buckets and put variable size objects.", wr_bucket)
        args = {'test_prefix': test_prefix_del, 'test_dir_path': self.test_dir_path,
                'skipget': True, 'skipdel': True, 'bkts_to_wr': wr_bucket, 'output': wr_output}
        self.ha_obj.put_get_delete(event, s3_test_obj, **args)
        wr_resp = tuple()
        while len(wr_resp) != 3:
            wr_resp = wr_output.get(timeout=HA_CFG["common_params"]["60sec_delay"])
        buckets = s3_test_obj.bucket_list()[1]
        assert_utils.assert_equal(len(buckets), wr_bucket, f"Failed to create {wr_bucket} number "
                                                           f"of buckets. Created {len(buckets)} "
                                                           "number of buckets")
        s3_data = wr_resp[0]
        LOGGER.info("Step 1: Successfully performed WRITEs with variable object sizes on %s "
                    "buckets for parallel DELETEs.", wr_bucket)

        LOGGER.info("Step 2: Perform WRITEs with variable object sizes for parallel READs")
        test_prefix_read = 'test-read-35781'
        resp = self.ha_obj.ha_s3_workload_operation(s3userinfo=list(users.values())[0],
                                                    log_prefix=test_prefix_read, skipread=True,
                                                    skipcleanup=True, nclients=5, nsamples=5)
        assert_utils.assert_true(resp[0], resp[1])
        LOGGER.info("Step 2: Performed WRITEs with variable sizes objects for parallel READs.")

        LOGGER.info("Starting three independent background threads for READs, WRITEs & DELETEs.")
        LOGGER.info("Step 3: Start Continuous DELETEs in background on random %s buckets",
                    del_bucket)
        bucket_list = list(s3_data.keys())
        get_random_buck = self.system_random.sample(bucket_list, del_bucket)
        args = {'test_prefix': test_prefix_del, 'test_dir_path': self.test_dir_path,
                'skipput': True, 'skipget': True, 'bkt_list': get_random_buck, 'output': del_output}
        thread_del = threading.Thread(target=self.ha_obj.put_get_delete,
                                      args=(event, s3_test_obj,), kwargs=args)
        thread_del.daemon = True  # Daemonize thread
        thread_del.start()
        LOGGER.info("Step 3: Successfully started DELETEs in background for %s buckets", del_bucket)

        LOGGER.info("Step 4: Perform WRITEs with variable object sizes in background")
        test_prefix_write = 'test-write-35781'
        output_wr = Queue()
        args = {'s3userinfo': list(users.values())[0], 'log_prefix': test_prefix_write,
                'nclients': 1, 'nsamples': 5, 'skipread': True, 'skipcleanup': True,
                'output': output_wr}
        thread_wri = threading.Thread(target=self.ha_obj.event_s3_operation, args=(event,),
                                      kwargs=args)
        thread_wri.daemon = True  # Daemonize thread
        thread_wri.start()
        LOGGER.info("Step 4: Successfully started WRITEs with variable sizes objects in background")

        LOGGER.info("Step 5: Perform READs and verify DI on the written data in background")
        output_rd = Queue()
        args = {'s3userinfo': list(users.values())[0], 'log_prefix': test_prefix_read,
                'nclients': 1, 'nsamples': 5, 'skipwrite': True, 'skipcleanup': True,
                'output': output_rd}
        thread_rd = threading.Thread(target=self.ha_obj.event_s3_operation, args=(event,),
                                     kwargs=args)
        thread_rd.daemon = True  # Daemonize thread
        thread_rd.start()
        LOGGER.info("Step 5: Successfully started READs and verified DI on the written data in "
                    "background")
        time.sleep(HA_CFG["common_params"]["30sec_delay"])

        LOGGER.info("Step 6: Shutdown %s (K) data pods one by one while continuous IOs in "
                    "background", self.kvalue)
        pod_list = self.node_master_list[0].get_all_pods(pod_prefix=const.POD_NAME_PREFIX)
        LOGGER.info("Get pod names to be deleted")
        self.pod_name_list = self.system_random.sample(pod_list, self.kvalue)
        event.set()
        for count, pod_name in enumerate(self.pod_name_list):
            count += 1
            LOGGER.info("Shutdown %s data pod %s by deleting deployment (unsafe)", count, pod_name)
            pod_data = list()
            pod_data.append(
                self.node_master_list[0].get_pod_hostname(pod_name=pod_name))  # hostname
            resp = self.node_master_list[0].delete_deployment(pod_name=pod_name)
            LOGGER.debug("Response: %s", resp)
            assert_utils.assert_false(resp[0], f"Failed to delete {count} pod {pod_name} by "
                                               f"deleting deployment (unsafe)")
            pod_data.append(resp[1])  # deployment_backup
            pod_data.append(resp[2])  # deployment_name
            self.restore_pod = self.deploy = True
            self.restore_method = const.RESTORE_DEPLOYMENT_K8S
            self.pod_dict[pod_name] = pod_data
            LOGGER.info("Deleted %s data pod %s by deleting deployment (unsafe)", count, pod_name)
        LOGGER.info("Step 6: Sucessfully shutdown %s (K) data pods one by one while continuous "
                    "IOs in background", self.kvalue)
        event.clear()

        LOGGER.info("Step 7: Check cluster status")
        resp = self.ha_obj.check_cluster_status(self.node_master_list[0])
        assert_utils.assert_false(resp[0], resp)
        LOGGER.info("Step 7: Cluster is in degraded state")

        LOGGER.info("Step 8: Check services status that were running on pods which are deleted.")
        counter = 0
        for pod_name in self.pod_name_list:
            hostname = self.pod_dict.get(pod_name)[0]
            resp = self.hlth_master_list[0].get_pod_svc_status(pod_list=[pod_name], fail=True,
                                                               hostname=hostname)
            LOGGER.debug("Services status on %s : %s", pod_name, resp)
            if not resp[0]:
                counter += 1
            pod_list.remove(pod_name)
        assert_utils.assert_equal(counter, 0, "Services on some pods not stopped.")
        LOGGER.info("Step 8: Services of pods are in offline state")

        LOGGER.info("Step 9: Check services status on remaining pods %s", pod_list)
        resp = self.hlth_master_list[0].get_pod_svc_status(pod_list=pod_list, fail=False)
        LOGGER.debug("Response: %s", resp)
        assert_utils.assert_true(resp[0], resp)
        LOGGER.info("Step 9: Services of remaining pods are in online state")

        LOGGER.info("Step 10: Verify status for In-flight READs/WRITEs/DELETEs while %s (K) "
                    "data pods were going down.", self.kvalue)
        LOGGER.info("Waiting for background IOs thread to join")
        thread_wri.join()
        thread_rd.join()
        thread_del.join()
        LOGGER.info("Step 10.1: Verify status for In-flight DELETEs while %s (K) data pods were"
                    "going down", self.kvalue)
        del_resp = tuple()
        while len(del_resp) != 2:
            del_resp = del_output.get(timeout=HA_CFG["common_params"]["60sec_delay"])
        if not del_resp:
            assert_utils.assert_true(False, "Background process failed to do deletes")
        fail_del_bkt = del_resp[1]
        rem_bkts_aftr_del = s3_test_obj.bucket_list()[1]
        assert_utils.assert_false(len(fail_del_bkt),
                                  f"Bucket deletion failed when cluster was online {fail_del_bkt}")
        assert_utils.assert_true(len(rem_bkts_aftr_del) < del_bucket,
                                 "Some bucket deletion expected during pods going down")
        LOGGER.info("Step 10.1: Verified status for In-flight DELETEs while %s (K) data pods were"
                    "going down", self.kvalue)

        LOGGER.info("Step 10.2: Verify status for In-flight WRITEs while %s (K) data pods going "
                    "down should be failed/error.", self.kvalue)
        responses_wr = dict()
        while len(responses_wr) != 2:
            responses_wr = output_wr.get(timeout=HA_CFG["common_params"]["60sec_delay"])
        pass_logs = list(x[1] for x in responses_wr["pass_res"])
        fail_logs = list(x[1] for x in responses_wr["fail_res"])
        resp = self.ha_obj.check_s3bench_log(file_paths=pass_logs)
        assert_utils.assert_false(len(resp[1]), f"Logs which contain failures: {resp[1]}")
        resp = self.ha_obj.check_s3bench_log(file_paths=fail_logs, pass_logs=False)
        # TODO: Expecting Failures when data pods going down. Re-test once CORTX-28541 is Resolved
        assert_utils.assert_true(len(resp[1]) <= len(fail_logs),
                                 f"Logs which contain pass: {resp[1]}")
        LOGGER.info("Step 10.2: Verified status for In-flight WRITEs while %s (K) data pods "
                    "going down.", self.kvalue)

        LOGGER.info("Step 10.3: Verify status for In-flight READs/Verify DI while %s (K) data pods "
                    "going down should be failed/error.", self.kvalue)
        responses_rd = dict()
        while len(responses_rd) != 2:
            responses_rd = output_rd.get(timeout=HA_CFG["common_params"]["60sec_delay"])
        pass_logs = list(x[1] for x in responses_rd["pass_res"])
        fail_logs = list(x[1] for x in responses_rd["fail_res"])
        resp = self.ha_obj.check_s3bench_log(file_paths=pass_logs)
        assert_utils.assert_false(len(resp[1]), f"Logs which contain failures: {resp[1]}")
        resp = self.ha_obj.check_s3bench_log(file_paths=fail_logs, pass_logs=False)
        # TODO: Expecting Failures when data pods going down. Re-test once CORTX-28541 is Resolved
        assert_utils.assert_true(len(resp[1]) <= len(fail_logs),
                                 f"Logs which contain pass: {resp[1]}")
        LOGGER.info("Step 10.3: Verified status for In-flight READs/Verify DI while %s (K) pods "
                    "going down.", self.kvalue)
        LOGGER.info("Step 10: Verified status for In-flight READs/WRITEs/DELETEs while %s (K) pods "
                    "were going down.", self.kvalue)

        LOGGER.info("Step 11: Perform IOs with variable sizes objects.")
        if CMN_CFG["dtm0_disabled"]:
            LOGGER.info("Step 11: Perform WRITEs-READs-Verify with variable object sizes "
                        "on degraded cluster with new user")
            users = self.mgnt_ops.create_account_users(nusers=1)
            test_prefix_read = 'test-35781-1'
            self.s3_clean.update(users)
        resp = self.ha_obj.ha_s3_workload_operation(s3userinfo=list(users.values())[0],
                                                    log_prefix=test_prefix_read,
                                                    nsamples=2, nclients=2, setup_s3bench=False)
        assert_utils.assert_true(resp[0], resp[1])
        LOGGER.info("Step 11: Performed IOs with variable sizes objects.")
        LOGGER.info("ENDED: Test to verify continuous READs/WRITEs/DELETEs while %s (K) pods "
                    "were going down.", self.kvalue)

    # pylint: disable=too-many-statements
    @pytest.mark.ha
    @pytest.mark.lc
    @pytest.mark.tags("TEST-35774")
    def test_degraded_writes_till_kpods_fail(self):
        """
        Test to verify degraded WRITEs after each pod failure till K data pods fail.
        """
        LOGGER.info("Started: Test to verify degraded Writes after each pod failure till K "
                    "data pods fail.")

        LOGGER.info("STEP 1: Perform WRITEs-READs-Verify with variable object sizes.")
        users = self.mgnt_ops.create_account_users(nusers=1)
        self.test_prefix = 'test-35774'
        self.s3_clean = users
        resp = self.ha_obj.ha_s3_workload_operation(s3userinfo=list(users.values())[0],
                                                    log_prefix=self.test_prefix,
                                                    skipcleanup=True, nsamples=2, nclients=2)
        assert_utils.assert_true(resp[0], resp[1])
        LOGGER.info("Step 1: Performed WRITEs-READs-Verify with variable sizes objects.")

        LOGGER.info("Step 2: Shutdown %s (K) data pods one by one and verify write/read/verify "
                    "after each pod down on new and existing buckets", self.kvalue)
        for count in range(1, self.kvalue+1):
            resp = self.ha_obj.delete_kpod_with_shutdown_methods(
                master_node_obj=self.node_master_list[0], health_obj=self.hlth_master_list[0],
                down_method=const.RESTORE_DEPLOYMENT_K8S)
            assert_utils.assert_true(resp[1], "Failed to shutdown/delete pod")
            pod_name = list(resp[1].keys())[0]
            pod_data = list()
            pod_data.append(resp[1][pod_name]['hostname'])  # hostname
            pod_data.append(resp[1][pod_name]['deployment_backup'])  # deployment_backup
            pod_data.append(resp[1][pod_name]['deployment_name'])  # deployment_name
            self.pod_dict[pod_name] = pod_data
            self.pod_name_list.append(pod_name)
            self.restore_pod = self.deploy = True
            self.restore_method = const.RESTORE_DEPLOYMENT_K8S
            assert_utils.assert_true(resp[0], "Cluster/Services status is not as expected")
            LOGGER.info("Deleted %s data pod %s by deleting deployment (unsafe)", count, pod_name)

            LOGGER.info("Step 3: Perform WRITEs, READs and verify DI on the already created "
                        "bucket")
            resp = self.ha_obj.ha_s3_workload_operation(s3userinfo=list(users.values())[0],
                                                        log_prefix=self.test_prefix,
                                                        skipcleanup=True, nsamples=2, nclients=2,
                                                        setup_s3bench=False)
            assert_utils.assert_true(resp[0], resp[1])
            LOGGER.info("Step 3: Successfully performed WRITEs, READs and verify DI on the "
                        "written data")

            if CMN_CFG["dtm0_disabled"]:
                LOGGER.info("STEP 4: Create IAM user and perform WRITEs-READs-Verify with "
                            "variable object sizes on degraded cluster")
                users = self.mgnt_ops.create_account_users(nusers=1)
                test_prefix_new = f'test-35774-{count}'
                self.s3_clean.update(users)
                resp = self.ha_obj.ha_s3_workload_operation(s3userinfo=list(users.values())[0],
                                                            log_prefix=test_prefix_new, nclients=2,
                                                            skipcleanup=True, nsamples=2,
                                                            setup_s3bench=False)
                assert_utils.assert_true(resp[0], resp[1])
                LOGGER.info("Step 4: Performed IOs with variable sizes objects.")

        LOGGER.info("%s (K) %s data pods shutdown one by one successfully and write/read/verify "
                    "after each pod down on new and existing buckets verified", self.kvalue,
                    self.pod_name_list)

        LOGGER.info("Completed: Test to verify degraded WRITEs after each pod failure till K "
                    "data pods fail.")

    @pytest.mark.ha
    @pytest.mark.lc
    @pytest.mark.tags("TEST-35775")
    def test_degraded_writes_kpods_failure(self):
        """
        Test to verify degraded WRITEs after all K data pods are failed.
        """
        LOGGER.info("Started: Test to verify degraded WRITEs after all K data pods are failed.")

        LOGGER.info("STEP 1: Perform WRITEs-READs-Verify with variable object sizes.")
        users = self.mgnt_ops.create_account_users(nusers=1)
        self.test_prefix = 'test-35775'
        self.s3_clean = users
        resp = self.ha_obj.ha_s3_workload_operation(s3userinfo=list(users.values())[0],
                                                    log_prefix=self.test_prefix,
                                                    skipcleanup=True, nsamples=2, nclients=2)
        assert_utils.assert_true(resp[0], resp[1])
        LOGGER.info("Step 1: Performed WRITEs-READs-Verify with variable sizes objects.")

        LOGGER.info("Step 2: Shutdown random %s (K) data pods by deleting deployment and "
                    "verify cluster & remaining pods status", self.kvalue)
        resp = self.ha_obj.delete_kpod_with_shutdown_methods(
            master_node_obj=self.node_master_list[0], health_obj=self.hlth_master_list[0],
            down_method=const.RESTORE_DEPLOYMENT_K8S, kvalue=self.kvalue)
        # Assert if empty dictionary
        assert_utils.assert_true(resp[1], "Failed to shutdown/delete pod")
        for pod_name in resp[1].keys():
            pod_data = list()
            pod_data.append(resp[1][pod_name]['hostname'])  # hostname
            pod_data.append(resp[1][pod_name]['deployment_backup'])  # deployment_backup
            pod_data.append(resp[1][pod_name]['deployment_name'])  # deployment_name
            self.pod_name_list.append(pod_name)
            self.pod_dict[pod_name] = pod_data
        self.restore_pod = self.deploy = True
        self.restore_method = const.RESTORE_DEPLOYMENT_K8S
        assert_utils.assert_true(resp[0], "Cluster/Services status is not as expected")
        LOGGER.info("Step 2: Successfully shutdown %s (K) data pod %s. Verified cluster and "
                    "services states are as expected & remaining pods status is online.",
                    self.kvalue, self.pod_name_list)

        LOGGER.info("Step 3: Perform WRITEs-READs-Verify and verify DI on the written data")
        resp = self.ha_obj.ha_s3_workload_operation(s3userinfo=list(users.values())[0],
                                                    log_prefix=self.test_prefix,
                                                    skipcleanup=True, nsamples=2, nclients=2,
                                                    setup_s3bench=False)
        assert_utils.assert_true(resp[0], resp[1])
        LOGGER.info("Step 3: Performed WRITEs-READs-Verify and verified DI on the written data")

        if CMN_CFG["dtm0_disabled"]:
            LOGGER.info("STEP 4: Create IAM user and perform WRITEs-READs-Verify with "
                        "variable object sizes on degraded cluster")
            users = self.mgnt_ops.create_account_users(nusers=1)
            self.test_prefix = 'test-35775-1'
            self.s3_clean.update(users)
            resp = self.ha_obj.ha_s3_workload_operation(s3userinfo=list(users.values())[0],
                                                        log_prefix=self.test_prefix,
                                                        skipcleanup=True, nsamples=2, nclients=2,
                                                        setup_s3bench=False)
            assert_utils.assert_true(resp[0], resp[1])
            LOGGER.info("Step 4: Performed IOs with variable sizes objects.")

        LOGGER.info("Completed: Test to verify degraded WRITEs after all K data pods are failed.")

    @pytest.mark.ha
    @pytest.mark.lc
    @pytest.mark.tags("TEST-35778")
    @pytest.mark.skip(reason="Buckets cruds won't be supported with DTM0")
    def test_degraded_deletes_till_kpods_fail(self):
        """
        Test to verify degraded DELETEs after each pod failure till K data pods fail.
        """
        LOGGER.info("Started: Test to verify degraded Deletes after each pod failure till K "
                    "data pods fail.")
        wr_bucket = HA_CFG["s3_bucket_data"]["no_buckets_for_deg_deletes"]
        del_bucket = 20
        event = threading.Event()
        wr_output = Queue()
        del_output = Queue()
        LOGGER.info("Step 1: Create %s buckets and perform WRITEs with variable size objects.",
                    wr_bucket)
        LOGGER.info("Create IAM user with name %s", self.s3acc_name)
        resp = self.rest_obj.create_s3_account(acc_name=self.s3acc_name,
                                               email_id=self.s3acc_email,
                                               passwd=S3_CFG["CliConfig"]["s3_account"]["password"])
        assert_utils.assert_true(resp[0], resp[1])
        access_key = resp[1]["access_key"]
        secret_key = resp[1]["secret_key"]
        self.test_prefix = 'test-35778'
        self.s3_clean = {'s3_acc': {'accesskey': access_key, 'secretkey': secret_key,
                                    'user_name': self.s3acc_name}}
        s3_test_obj = S3TestLib(access_key=access_key, secret_key=secret_key,
                                endpoint_url=S3_CFG["s3_url"])
        LOGGER.info("Successfully created IAM user with name %s", self.s3acc_name)

        LOGGER.info("Create %s buckets and put variable size objects.", wr_bucket)
        args = {'test_prefix': self.test_prefix, 'test_dir_path': self.test_dir_path,
                'skipget': True, 'skipdel': True, 'bkts_to_wr': wr_bucket, 'output': wr_output}

        self.ha_obj.put_get_delete(event, s3_test_obj, **args)
        wr_resp = tuple()
        while len(wr_resp) != 3:
            wr_resp = wr_output.get(timeout=HA_CFG["common_params"]["60sec_delay"])
        s3_data = wr_resp[0]  # Contains s3 data for passed buckets
        buckets = s3_test_obj.bucket_list()[1]
        assert_utils.assert_equal(len(buckets), wr_bucket, f"Failed to create {wr_bucket} number "
                                                           f"of buckets. Created {len(buckets)} "
                                                           "number of buckets")
        LOGGER.info("Step 1: Successfully created %s buckets & "
                    "perform WRITEs with variable size objects.", wr_bucket)

        LOGGER.info("Step 2: Shutdown %s (K) data pods one by one and perform Delete on random %s"
                    " buckets and verify read on remaining bucket after each pod down",
                    self.kvalue, del_bucket)
        for count in range(1, self.kvalue+1):
            resp = self.ha_obj.delete_kpod_with_shutdown_methods(
                master_node_obj=self.node_master_list[0], health_obj=self.hlth_master_list[0],
                down_method=const.RESTORE_DEPLOYMENT_K8S)
            assert_utils.assert_true(resp[1], "Failed to shutdown/delete pod")
            pod_name = list(resp[1].keys())[0]
            pod_data = list()
            pod_data.append(resp[1][pod_name]['hostname'])  # hostname
            pod_data.append(resp[1][pod_name]['deployment_backup'])  # deployment_backup
            pod_data.append(resp[1][pod_name]['deployment_name'])  # deployment_name
            self.pod_dict[pod_name] = pod_data
            self.pod_name_list.append(pod_name)
            self.restore_pod = self.deploy = True
            self.restore_method = const.RESTORE_DEPLOYMENT_K8S
            assert_utils.assert_true(resp[0], "Cluster/Services status is not as expected")
            LOGGER.info("Deleted %s data pod %s by deleting deployment (unsafe)", count, pod_name)

            LOGGER.info("Step 3: Perform DELETEs on random %s buckets", del_bucket)
            args = {'test_prefix': self.test_prefix, 'test_dir_path': self.test_dir_path,
                    'skipput': True, 'skipget': True, 'bkts_to_del': del_bucket,
                    'output': del_output}

            self.ha_obj.put_get_delete(event, s3_test_obj, **args)
            del_resp = tuple()
            while len(del_resp) != 2:
                del_resp = del_output.get(timeout=HA_CFG["common_params"]["60sec_delay"])
            remain_bkt = s3_test_obj.bucket_list()[1]
            assert_utils.assert_equal(len(remain_bkt), wr_bucket - del_bucket,
                                      f"Failed to delete {del_bucket} number of buckets from "
                                      f"{wr_bucket}. Remaining {len(remain_bkt)} number of "
                                      "buckets")
            LOGGER.info("Step 3: Successfully performed DELETEs on random %s buckets", del_bucket)
            wr_bucket = len(remain_bkt)

            LOGGER.info("Step 4: Perform READs on the remaining %s buckets", remain_bkt)
            rd_output = Queue()
            new_s3data = dict()
            for bkt in remain_bkt:
                new_s3data[bkt] = s3_data[bkt]

            args = {'test_prefix': self.test_prefix, 'test_dir_path': self.test_dir_path,
                    'skipput': True, 'skipdel': True, 's3_data': new_s3data, 'di_check': True,
                    'output': rd_output}
            self.ha_obj.put_get_delete(event, s3_test_obj, **args)
            rd_resp = tuple()
            while len(rd_resp) != 4:
                rd_resp = rd_output.get(timeout=HA_CFG["common_params"]["60sec_delay"])
            event_bkt_get = rd_resp[0]
            fail_bkt_get = rd_resp[1]
            event_di_bkt = rd_resp[2]
            fail_di_bkt = rd_resp[3]

            # Above four lists are expected to be empty as all pass expected
            assert_utils.assert_false(len(fail_bkt_get) or len(fail_di_bkt) or len(event_bkt_get)
                                      or len(event_di_bkt), "Expected pass in read and di check "
                                                            "operations. Found failures in READ: "
                                                            f"{fail_bkt_get} {event_bkt_get}"
                                                            f"or DI_CHECK: {fail_di_bkt} "
                                                            f"{event_di_bkt}")
            LOGGER.info("Step 4: Successfully performed READs on the remaining %s buckets.",
                        remain_bkt)

        LOGGER.info("Shutdown %s (K) %s data pods one by one and performed Deletes on random "
                    "buckets and verified read on remaining bucket after each pod down",
                    self.kvalue, self.pod_name_list)

        LOGGER.info("Completed: Test to verify degraded DELETEs after each pod failure till K "
                    "data pods fail.")

    @pytest.mark.ha
    @pytest.mark.lc
    @pytest.mark.tags("TEST-35785")
    def test_copy_object_kpods_fail(self):
        """
        Test to Verify copy object when all K data pods are failed.
        """
        LOGGER.info("STARTED: Test to Verify copy object when all K data pods are failed.")

        bkt_cnt = HA_CFG["copy_obj_data"]["bkt_cnt"]
        bkt_obj_dict = dict()
        for cnt in range(bkt_cnt):
            t_t = int(perf_counter_ns())
            bkt_obj_dict[f"ha-bkt-{cnt}-{t_t}"] = f"ha-obj-{cnt}-{t_t}"
        event = threading.Event()
        LOGGER.info("Creating IAM user with name %s", self.s3acc_name)
        resp = self.rest_obj.create_s3_account(acc_name=self.s3acc_name,
                                               email_id=self.s3acc_email,
                                               passwd=S3_CFG["CliConfig"]["s3_account"]["password"])
        assert_utils.assert_true(resp[0], resp[1])
        access_key = resp[1]["access_key"]
        secret_key = resp[1]["secret_key"]
        s3_test_obj = S3TestLib(access_key=access_key, secret_key=secret_key,
                                endpoint_url=S3_CFG["s3_url"])
        LOGGER.info("Successfully created IAM user with name %s", self.s3acc_name)
        self.s3_clean = {'s3_acc': {'accesskey': access_key, 'secretkey': secret_key,
                                    'user_name': self.s3acc_name}}
        LOGGER.info("Step 1: Create and list buckets and perform upload and copy "
                    "object from %s bucket to other buckets ", self.bucket_name)
        resp = self.ha_obj.create_bucket_copy_obj(event, s3_test_obj=s3_test_obj,
                                                  bucket_name=self.bucket_name,
                                                  object_name=self.object_name,
                                                  bkt_obj_dict=bkt_obj_dict,
                                                  file_path=self.multipart_obj_path)
        assert_utils.assert_true(resp[0], f"Failed buckets are: {resp[1]}")
        put_etag = resp[1]
        LOGGER.info("Step 1: successfully create and list buckets and perform upload and copy"
                    "object from %s bucket to other buckets", self.bucket_name)

        LOGGER.info("Step 2: Shutdown random %s (K) data pods by deleting deployment and "
                    "verify cluster & remaining pods status", self.kvalue)
        resp = self.ha_obj.delete_kpod_with_shutdown_methods(
            master_node_obj=self.node_master_list[0], health_obj=self.hlth_master_list[0],
            down_method=const.RESTORE_DEPLOYMENT_K8S, kvalue=self.kvalue)
        # Assert if empty dictionary
        assert_utils.assert_true(resp[1], "Failed to shutdown/delete pod")
        for pod_name in resp[1].keys():
            pod_data = list()
            pod_data.append(resp[1][pod_name]['hostname'])  # hostname
            pod_data.append(resp[1][pod_name]['deployment_backup'])  # deployment_backup
            pod_data.append(resp[1][pod_name]['deployment_name'])  # deployment_name
            self.pod_name_list.append(pod_name)
            self.pod_dict[pod_name] = pod_data
        self.restore_pod = self.deploy = True
        self.restore_method = const.RESTORE_DEPLOYMENT_K8S
        assert_utils.assert_true(resp[0], "Cluster/Services status is not as expected")
        LOGGER.info("Step 2: Successfully shutdown %s (K) data pod %s. Verified cluster and "
                    "services states are as expected & remaining pods status is online.",
                    self.kvalue, self.pod_name_list)

        LOGGER.info("Step 3: Download the uploaded objects & verify etags")
        for key, val in bkt_obj_dict.items():
            resp = s3_test_obj.get_object(bucket=key, key=val)
            LOGGER.info("Get object response: %s", resp)
            get_etag = resp[1]["ETag"]
            assert_utils.assert_equal(put_etag, get_etag, "Failed in Etag verification of "
                                                          f"object {key} of bucket {val}. "
                                                          "Put and Get Etag mismatch")
        LOGGER.info("Step 3: Successfully download the uploaded objects & verify etags")

        t_t = int(perf_counter_ns())
        bucket_name = self.bucket_name
        bkt_op = False
        if CMN_CFG["dtm0_disabled"]:
            LOGGER.info("Create and list buckets")
            bkt_obj_dict.clear()
            for cnt in range(bkt_cnt):
                bkt_obj_dict[f"ha-bkt{cnt}-{t_t}"] = f"ha-obj{cnt}-{t_t}"
            bucket_name = f"ha-mp-bkt-{t_t}-2"
            bkt_op = True
        else:
            for idx, bkt in enumerate(bkt_obj_dict):
                bkt_obj_dict[bkt] = f"ha-obj{idx}-{t_t}"

        LOGGER.info("Step 4: Perform copy object from %s bucket to other buckets verify copy object"
                    " etags", bucket_name)
        resp = self.ha_obj.create_bucket_copy_obj(event, s3_test_obj=s3_test_obj,
                                                  bucket_name=bucket_name,
                                                  object_name=self.object_name,
                                                  bkt_obj_dict=bkt_obj_dict,
                                                  put_etag=put_etag,
                                                  bkt_op=bkt_op)
        assert_utils.assert_true(resp[0], f"Failed buckets are: {resp[1]}")
        LOGGER.info("Step 4: Performed copy object from %s bucket to other buckets verified copy "
                    "object etags", bucket_name)

        LOGGER.info("Step 5: Download the copied objects & verify etags.")
        for bkt, obj in bkt_obj_dict.items():
            resp = s3_test_obj.get_object(bucket=bkt, key=obj)
            LOGGER.info("Get object response: %s", resp)
            get_etag = resp[1]["ETag"]
            assert_utils.assert_equal(put_etag, get_etag, "Failed in verification of Put & Get "
                                                          f"Etag for object {obj} of bucket "
                                                          f"{bkt}.")
        LOGGER.info("Step 5: Downloaded copied objects & verify etags.")

        LOGGER.info("COMPLETED: Test to Verify copy object when all K data pods are failed.")

    @pytest.mark.ha
    @pytest.mark.lc
    @pytest.mark.tags("TEST-35782")
    def test_degraded_mpu_after_kpods_fail(self):
        """
        This test tests multipart upload after all K data pods are failed
        """
        LOGGER.info("STARTED: Test to verify multipart upload after all K data pods are failed.")
        file_size = HA_CFG["5gb_mpu_data"]["file_size"]
        total_parts = HA_CFG["5gb_mpu_data"]["total_parts"]
        download_file = self.test_file + "_download"
        download_path = os.path.join(self.test_dir_path, download_file)

        LOGGER.info("Step 1: Create bucket and perform multipart upload of size %sMB.",
                    file_size)
        LOGGER.info("Creating IAM user with name %s", self.s3acc_name)
        resp = self.rest_obj.create_s3_account(acc_name=self.s3acc_name,
                                               email_id=self.s3acc_email,
                                               passwd=S3_CFG["CliConfig"]["s3_account"]["password"])
        assert_utils.assert_true(resp[0], resp[1])
        access_key = resp[1]["access_key"]
        secret_key = resp[1]["secret_key"]
        s3_test_obj = S3TestLib(access_key=access_key, secret_key=secret_key,
                                endpoint_url=S3_CFG["s3_url"])
        LOGGER.info("Successfully created IAM user with name %s", self.s3acc_name)
        self.s3_clean = {'s3_acc': {'accesskey': access_key, 'secretkey': secret_key,
                                    'user_name': self.s3acc_name}}
        resp = self.ha_obj.create_bucket_to_complete_mpu(s3_data=self.s3_clean,
                                                         bucket_name=self.bucket_name,
                                                         object_name=self.object_name,
                                                         file_size=file_size,
                                                         total_parts=total_parts,
                                                         multipart_obj_path=self.multipart_obj_path)
        assert_utils.assert_true(resp[0], resp)
        result = s3_test_obj.object_info(self.bucket_name, self.object_name)
        obj_size = result[1]["ContentLength"]
        LOGGER.debug("Uploaded object of size %s for %s", obj_size, self.bucket_name)
        assert_utils.assert_equal(obj_size, file_size * const.Sizes.MB)
        upload_checksum = str(resp[2])
        LOGGER.info("Step 1: Successfully performed multipart upload for size size %sMB.",
                    file_size)

        LOGGER.info("Step 2: Shutdown random %s (K) data pods by deleting deployment and "
                    "verify cluster & remaining pods status", self.kvalue)
        resp = self.ha_obj.delete_kpod_with_shutdown_methods(
            master_node_obj=self.node_master_list[0], health_obj=self.hlth_master_list[0],
            down_method=const.RESTORE_DEPLOYMENT_K8S, kvalue=self.kvalue)
        # Assert if empty dictionary
        assert_utils.assert_true(resp[1], "Failed to shutdown/delete pod")
        for pod_name in resp[1].keys():
            pod_data = list()
            pod_data.append(resp[1][pod_name]['hostname'])  # hostname
            pod_data.append(resp[1][pod_name]['deployment_backup'])  # deployment_backup
            pod_data.append(resp[1][pod_name]['deployment_name'])  # deployment_name
            self.pod_name_list.append(pod_name)
            self.pod_dict[pod_name] = pod_data
        self.restore_pod = self.deploy = True
        self.restore_method = const.RESTORE_DEPLOYMENT_K8S
        assert_utils.assert_true(resp[0], "Cluster/Services status is not as expected")
        LOGGER.info("Step 2: Successfully shutdown %s (K) data pod %s. Verified cluster and "
                    "services states are as expected & remaining pods status is online.",
                    self.kvalue, self.pod_name_list)

        LOGGER.info("Step 3: Download the uploaded object and verify checksum")
        resp = s3_test_obj.object_download(self.bucket_name, self.object_name, download_path)
        assert_utils.assert_true(resp[0], resp[1])
        download_checksum = self.ha_obj.cal_compare_checksum(file_list=[download_path],
                                                             compare=False)[0]
        assert_utils.assert_equal(upload_checksum, download_checksum,
                                  f"Failed to match checksum: {upload_checksum},"
                                  f" {download_checksum}")
        LOGGER.info("Matched checksum: %s, %s", upload_checksum, download_checksum)
        LOGGER.info("Step 3: Successfully downloaded the object and verified the checksum")

        LOGGER.info("Removing files %s and %s", self.multipart_obj_path, download_path)
        sysutils.remove_file(self.multipart_obj_path)
        sysutils.remove_file(download_path)

        LOGGER.info("Step 4: Do multipart upload and download the object & verify checksum.")
        if CMN_CFG["dtm0_disabled"]:
            LOGGER.info("Creating new bucket for new upload")
            bucket_name = f"mp-bkt-{perf_counter_ns()}"
        else:
            bucket_name = self.bucket_name
        object_name = f"mp-obj-{int(perf_counter_ns())}"
        resp = self.ha_obj.create_bucket_to_complete_mpu(s3_data=self.s3_clean,
                                                         bucket_name=bucket_name,
                                                         object_name=object_name,
                                                         file_size=file_size,
                                                         total_parts=total_parts,
                                                         multipart_obj_path=self.multipart_obj_path)
        assert_utils.assert_true(resp[0], resp)
        upload_checksum1 = resp[2]
        result = s3_test_obj.object_info(bucket_name, object_name)
        obj_size = result[1]["ContentLength"]
        LOGGER.debug("Uploaded object info for %s is %s", bucket_name, result)
        assert_utils.assert_equal(obj_size, file_size * const.Sizes.MB)

        resp = s3_test_obj.object_download(bucket_name, object_name, download_path)
        LOGGER.info("Download object response: %s", resp)
        assert_utils.assert_true(resp[0], resp[1])
        download_checksum1 = self.ha_obj.cal_compare_checksum(file_list=[download_path],
                                                              compare=False)[0]
        assert_utils.assert_equal(upload_checksum1, download_checksum1,
                                  f"Failed to match checksum: {upload_checksum1},"
                                  f" {download_checksum1}")
        LOGGER.info("Matched checksum: %s, %s", upload_checksum1, download_checksum1)
        LOGGER.info("Step 4: Did multipart upload & downloaded the object & verified the checksum.")

        LOGGER.info("COMPLETED: Test to verify multipart upload after all K data pods are failed.")

    @pytest.mark.ha
    @pytest.mark.lc
    @pytest.mark.tags("TEST-35786")
    def test_copy_object_during_kpods_down(self):
        """
        Test to verify copy object during data pods failure till K pods.
        """
        LOGGER.info("STARTED: Verify copy object during data pods failure till K pods.")

        bkt_obj_dict = dict()
        output = Queue()
        bkt_obj_dict[f"ha-bkt-{perf_counter_ns()}"] = f"ha-obj-{perf_counter_ns()}"
        event = threading.Event()

        LOGGER.info("Creating IAM user with name %s", self.s3acc_name)
        resp = self.rest_obj.create_s3_account(acc_name=self.s3acc_name,
                                               email_id=self.s3acc_email,
                                               passwd=S3_CFG["CliConfig"]["s3_account"]["password"])
        assert_utils.assert_true(resp[0], resp[1])
        access_key = resp[1]["access_key"]
        secret_key = resp[1]["secret_key"]
        s3_test_obj = S3TestLib(access_key=access_key, secret_key=secret_key,
                                endpoint_url=S3_CFG["s3_url"])
        LOGGER.info("Successfully created IAM user with name %s", self.s3acc_name)
        self.s3_clean = {'s3_acc': {'accesskey': access_key, 'secretkey': secret_key,
                                    'user_name': self.s3acc_name}}

        LOGGER.info("Step 1: Create bucket, upload an object and copy to the bucket")
        # This is done just to get put_etag for further ops.
        resp = self.ha_obj.create_bucket_copy_obj(event, s3_test_obj=s3_test_obj,
                                                  bucket_name=self.bucket_name,
                                                  object_name=self.object_name,
                                                  bkt_obj_dict=bkt_obj_dict,
                                                  file_path=self.multipart_obj_path)
        assert_utils.assert_true(resp[0], resp[1])
        put_etag = resp[1]
        LOGGER.info("Step 1: Successfully created bucket, uploaded and copied an object "
                    "to the bucket")
        bkt_obj_dict.clear()

        bkt_cnt = HA_CFG["copy_obj_data"]["bkt_multi"]
        for cnt in range(bkt_cnt):
            rd_time = perf_counter_ns()
            s3_test_obj.create_bucket(f"ha-bkt{cnt}-{rd_time}")
            bkt_obj_dict[f"ha-bkt{cnt}-{rd_time}"] = f"ha-obj{cnt}-{rd_time}"
        LOGGER.info("Step 2: Create multiple buckets and copy object from %s to other buckets in "
                    "background", self.bucket_name)
        args = {'s3_test_obj': s3_test_obj, 'bucket_name': self.bucket_name,
                'object_name': self.object_name, 'bkt_obj_dict': bkt_obj_dict, 'output': output,
                'file_path': self.multipart_obj_path, 'background': True, 'bkt_op': False,
                'put_etag': put_etag}
        thread = threading.Thread(target=self.ha_obj.create_bucket_copy_obj, args=(event,),
                                  kwargs=args)
        thread.daemon = True  # Daemonize thread
        thread.start()
        LOGGER.info("Step 2: Successfully started background process for copy object")
        # While loop to sync this operation with background thread to achieve expected scenario
        LOGGER.info("Waiting for creation of %s buckets", bkt_cnt)
        bkt_list = list()
        timeout = time.time() + 60 * 3
        while len(bkt_list) < bkt_cnt:
            time.sleep(HA_CFG["common_params"]["20sec_delay"])
            bkt_list = s3_test_obj.bucket_list()[1]
            if timeout < time.time():
                LOGGER.error("Bucket creation is taking longer than 3 mins")
                assert_utils.assert_true(False, "Please check background process logs")
        time.sleep(HA_CFG["common_params"]["20sec_delay"])

        LOGGER.info("Step 3: Shutdown random %s (K) data pods by deleting deployment and "
                    "verify cluster & remaining pods status", self.kvalue)
        resp = self.ha_obj.delete_kpod_with_shutdown_methods(
            master_node_obj=self.node_master_list[0], health_obj=self.hlth_master_list[0],
            down_method=const.RESTORE_DEPLOYMENT_K8S, kvalue=self.kvalue, event=event)
        # Assert if empty dictionary
        assert_utils.assert_true(resp[1], "Failed to shutdown/delete pod")
        for pod_name in resp[1].keys():
            pod_data = list()
            pod_data.append(resp[1][pod_name]['hostname'])  # hostname
            pod_data.append(resp[1][pod_name]['deployment_backup'])  # deployment_backup
            pod_data.append(resp[1][pod_name]['deployment_name'])  # deployment_name
            self.pod_name_list.append(pod_name)
            self.pod_dict[pod_name] = pod_data
        self.restore_pod = self.deploy = True
        self.restore_method = const.RESTORE_DEPLOYMENT_K8S
        assert_utils.assert_true(resp[0], "Cluster/Services status is not as expected")
        LOGGER.info("Step 3: Successfully shutdown %s (K) data pod %s. Verified cluster and "
                    "services states are as expected & remaining pods status is online.",
                    self.kvalue, self.pod_name_list)

        LOGGER.info("Step 4: Checking responses from background copy process")
        thread.join()
        responses = tuple()
        while len(responses) < 3:
            responses = output.get(timeout=HA_CFG["common_params"]["60sec_delay"])

        if not responses:
            assert_utils.assert_true(False, "Background process failed to do copy object")

        put_etag = responses[0]
        exp_fail_bkt_obj_dict = responses[1]
        failed_bkts = responses[2]
        LOGGER.debug("Responses received from background process:\nput_etag: "
                     "%s\nexp_fail_bkt_obj_dict: %s\nfailed_bkts: %s", put_etag,
                     exp_fail_bkt_obj_dict, failed_bkts)
        if len(exp_fail_bkt_obj_dict) == 0 and len(failed_bkts) == 0:
            LOGGER.info("Copy object operation for all the buckets completed successfully. ")
        elif failed_bkts:
            assert_utils.assert_true(False, "Failed to do copy object when cluster was in degraded "
                                            f"state. Failed buckets: {failed_bkts}")
        elif exp_fail_bkt_obj_dict:
            LOGGER.info("Step 4.1: Retrying copy object to buckets %s",
                        list(exp_fail_bkt_obj_dict.keys()))
            resp = self.ha_obj.create_bucket_copy_obj(event, s3_test_obj=s3_test_obj,
                                                      bucket_name=self.bucket_name,
                                                      object_name=self.object_name,
                                                      bkt_obj_dict=exp_fail_bkt_obj_dict,
                                                      bkt_op=False, put_etag=put_etag)
            assert_utils.assert_true(resp[0], f"Failed buckets are: {resp[1]}")
            put_etag = resp[1]
        LOGGER.info("Step 4: Successfully checked and completed background copy process")

        LOGGER.info("Step 5: Download the uploaded objects & verify etags")
        for key, val in bkt_obj_dict.items():
            resp = s3_test_obj.get_object(bucket=key, key=val)
            LOGGER.info("Get object response: %s", resp)
            get_etag = resp[1]["ETag"]
            assert_utils.assert_equal(put_etag, get_etag, "Failed in Etag verification of "
                                                          f"object {key} of bucket {val}. "
                                                          "Put and Get Etag mismatch")
        LOGGER.info("Step 5: Successfully download the uploaded objects & verify etags")

        t_t = int(perf_counter_ns())
        bucket_name = self.bucket_name
        bkt_op = False
        if CMN_CFG["dtm0_disabled"]:
            LOGGER.info("Create and list buckets")
            bkt_obj_dict.clear()
            for cnt in range(bkt_cnt):
                bkt_obj_dict[f"ha-bkt{cnt}-{t_t}"] = f"ha-obj{cnt}-{t_t}"
            bucket_name = f"ha-mp-bkt-{t_t}-2"
            bkt_op = True
        else:
            for idx, bkt in enumerate(bkt_obj_dict):
                bkt_obj_dict[bkt] = f"ha-obj{idx}-{t_t}"

        LOGGER.info("Step 6: Perform copy object from %s bucket to other buckets verify copy object"
                    " etags", bucket_name)
        resp = self.ha_obj.create_bucket_copy_obj(event, s3_test_obj=s3_test_obj,
                                                  bucket_name=bucket_name,
                                                  object_name=self.object_name,
                                                  bkt_obj_dict=bkt_obj_dict,
                                                  put_etag=put_etag,
                                                  bkt_op=bkt_op)
        assert_utils.assert_true(resp[0], f"Failed buckets are: {resp[1]}")
        LOGGER.info("Step 6: Performed copy object from %s bucket to other buckets verified copy "
                    "object etags", bucket_name)

        LOGGER.info("Step 7: Download the copied objects & verify etags.")
        for bkt, obj in bkt_obj_dict.items():
            resp = s3_test_obj.get_object(bucket=bkt, key=obj)
            LOGGER.info("Get object response: %s", resp)
            get_etag = resp[1]["ETag"]
            assert_utils.assert_equal(put_etag, get_etag, "Failed in verification of Put & Get "
                                                          f"Etag for object {obj} of bucket "
                                                          f"{bkt}.")
        LOGGER.info("Step 7: Downloaded copied objects & verify etags.")

        LOGGER.info("COMPLETED: Verify copy object during data pods failure till K pods.")

    @pytest.mark.ha
    @pytest.mark.lc
    @pytest.mark.tags("TEST-35783")
    def test_partial_mpu_after_kpods_fail(self):
        """
        This test tests partial multipart upload after each data pod is failed till K
        pods and complete upload after all K data pods are failed
        """
        LOGGER.info("STARTED: Test to verify partial multipart upload after each data pod is "
                    "failed till K data pods and complete upload after all K data pods are failed")
        file_size = HA_CFG["5gb_mpu_data"]["file_size"]
        total_parts = self.kvalue * 5 + HA_CFG["5gb_mpu_data"]["total_parts"]
        parts = list(range(1, total_parts + 1))
        download_file = self.test_file + "_download"
        download_path = os.path.join(self.test_dir_path, download_file)
        if os.path.exists(self.multipart_obj_path):
            os.remove(self.multipart_obj_path)
        sysutils.create_file(self.multipart_obj_path, file_size)
        LOGGER.info("Calculating checksum of file %s", self.multipart_obj_path)
        upload_checksum = self.ha_obj.cal_compare_checksum(file_list=[self.multipart_obj_path],
                                                           compare=False)[0]

        LOGGER.info("Creating IAM user with name %s", self.s3acc_name)
        resp = self.rest_obj.create_s3_account(acc_name=self.s3acc_name,
                                               email_id=self.s3acc_email,
                                               passwd=S3_CFG["CliConfig"]["s3_account"]["password"])
        assert_utils.assert_true(resp[0], resp[1])
        access_key = resp[1]["access_key"]
        secret_key = resp[1]["secret_key"]
        s3_test_obj = S3TestLib(access_key=access_key, secret_key=secret_key,
                                endpoint_url=S3_CFG["s3_url"])
        s3_mp_test_obj = S3MultipartTestLib(access_key=access_key, secret_key=secret_key,
                                            endpoint_url=S3_CFG["s3_url"])
        LOGGER.info("Successfully created IAM user with name %s", self.s3acc_name)
        self.s3_clean = {'s3_acc': {'accesskey': access_key, 'secretkey': secret_key,
                                    'user_name': self.s3acc_name}}
        uploading_parts = self.system_random.sample(parts, 10)
        LOGGER.info("Step 1: Perform partial multipart upload for %s parts out of total %s",
                    uploading_parts, len(parts))
        resp = self.ha_obj.partial_multipart_upload(s3_data=self.s3_clean,
                                                    bucket_name=self.bucket_name,
                                                    object_name=self.object_name,
                                                    part_numbers=uploading_parts,
                                                    multipart_obj_size=file_size,
                                                    total_parts=total_parts,
                                                    multipart_obj_path=self.multipart_obj_path)
        mpu_id = resp[1]
        object_path = resp[2]
        parts_etags = copy.deepcopy(resp[3])
        assert_utils.assert_true(resp[0], f"Failed to upload parts. Response: {resp}")
        LOGGER.info("Listing parts of partial multipart upload")
        res = s3_mp_test_obj.list_parts(mpu_id, self.bucket_name, self.object_name)
        assert_utils.assert_true(res[0], res)
        for part_n in res[1]["Parts"]:
            assert_utils.assert_list_item(uploading_parts, part_n["PartNumber"])
        LOGGER.info("Listed parts of partial multipart upload: %s", res[1])
        LOGGER.info("Step 1: Successfully performed partial multipart upload for %s parts out "
                    "of total %s", uploading_parts, len(parts))
        parts = [ele for ele in parts if ele not in uploading_parts]

        LOGGER.info("Step 2: Shutdown %s (K) data pods one by one and start multipart upload for "
                    "%s size object in %s parts with every data pod shutdown.",
                    self.kvalue, file_size * const.Sizes.MB, total_parts)
        for count in range(1, self.kvalue+1):
            resp = self.ha_obj.delete_kpod_with_shutdown_methods(
                master_node_obj=self.node_master_list[0], health_obj=self.hlth_master_list[0],
                down_method=const.RESTORE_DEPLOYMENT_K8S)
            assert_utils.assert_true(resp[1], "Failed to shutdown/delete pod")
            pod_name = list(resp[1].keys())[0]
            pod_data = list()
            pod_data.append(resp[1][pod_name]['hostname'])  # hostname
            pod_data.append(resp[1][pod_name]['deployment_backup'])  # deployment_backup
            pod_data.append(resp[1][pod_name]['deployment_name'])  # deployment_name
            self.pod_dict[pod_name] = pod_data
            self.pod_name_list.append(pod_name)
            self.restore_pod = self.deploy = True
            self.restore_method = const.RESTORE_DEPLOYMENT_K8S
            assert_utils.assert_true(resp[0], "Cluster/Services status is not as expected")
            LOGGER.info("Deleted %s pod %s by deleting deployment (unsafe)", count, pod_name)

            uploading_parts = self.system_random.sample(parts, 10)
            LOGGER.info("Step 3: Perform partial multipart upload for %s parts out of total %s",
                        uploading_parts, len(parts))
            resp = self.ha_obj.partial_multipart_upload(s3_data=self.s3_clean,
                                                        bucket_name=self.bucket_name,
                                                        object_name=self.object_name,
                                                        part_numbers=uploading_parts,
                                                        remaining_upload=True, mpu_id=mpu_id,
                                                        multipart_obj_size=file_size,
                                                        total_parts=total_parts,
                                                        multipart_obj_path=object_path)

            assert_utils.assert_true(resp[0], f"Failed to upload parts {resp[1]}")
            LOGGER.info("Listing parts of partial multipart upload")
            res = s3_mp_test_obj.list_parts(mpu_id, self.bucket_name, self.object_name)
            assert_utils.assert_true(res[0], res)
            for part_n in res[1]["Parts"]:
                assert_utils.assert_list_item(uploading_parts, part_n["PartNumber"])
            LOGGER.info("Listed parts of partial multipart upload: %s", res[1])
            parts_etags.extend(resp[3])
            parts = [ele for ele in parts if ele not in uploading_parts]
            LOGGER.info("Step 3: Successfully performed partial multipart upload for %s parts out "
                        "of total %s", uploading_parts, len(parts))
        LOGGER.info("Step 2: Successfully shutdown %s (K) data pods one by one and uploaded "
                    "total %s parts", self.kvalue, uploading_parts)

        LOGGER.info("Step 4: Upload remaining %s parts", parts)
        resp = self.ha_obj.partial_multipart_upload(s3_data=self.s3_clean,
                                                    bucket_name=self.bucket_name,
                                                    object_name=self.object_name,
                                                    part_numbers=parts,
                                                    remaining_upload=True, mpu_id=mpu_id,
                                                    multipart_obj_size=file_size,
                                                    total_parts=total_parts,
                                                    multipart_obj_path=object_path)

        assert_utils.assert_true(resp[0], f"Failed to upload remaining parts {resp[1]}")
        parts_etags.extend(resp[3])
        LOGGER.info("Listing parts of partial multipart upload")
        res = s3_mp_test_obj.list_parts(mpu_id, self.bucket_name, self.object_name)
        assert_utils.assert_true(res[0], res)
        for part_n in res[1]["Parts"]:
            assert_utils.assert_list_item(parts, part_n["PartNumber"])
        LOGGER.info("Listed parts of partial multipart upload: %s", res[1])
        LOGGER.info("Step 4: Successfully uploaded remaining parts")

        parts_etag = sorted(parts_etags, key=lambda d: d['PartNumber'])

        LOGGER.info("Step 5: Listing parts of multipart upload")
        res = s3_mp_test_obj.list_parts(mpu_id, self.bucket_name, self.object_name)
        assert_utils.assert_true(res[0], res)
        assert_utils.assert_equal(len(res[1]["Parts"]), total_parts)
        LOGGER.info("Step 5: Listed parts of multipart upload. Count: %s", len(res[1]["Parts"]))

        LOGGER.info("Step 6: Completing multipart upload & verified upload size is %s",
                    file_size * const.Sizes.MB)
        res = s3_mp_test_obj.complete_multipart_upload(mpu_id, parts_etag, self.bucket_name,
                                                       self.object_name)
        assert_utils.assert_true(res[0], res)
        res = s3_test_obj.object_list(self.bucket_name)
        if self.object_name not in res[1]:
            assert_utils.assert_true(False, res)
        result = s3_test_obj.object_info(self.bucket_name, self.object_name)
        obj_size = result[1]["ContentLength"]
        LOGGER.debug("Uploaded object info for %s is %s", self.bucket_name, result)
        assert_utils.assert_equal(obj_size, file_size * const.Sizes.MB)
        LOGGER.info("Step 6: Multipart upload completed & verified upload size is %s",
                    file_size * const.Sizes.MB)

        LOGGER.info("Step 7: Download the uploaded object and verify checksum")
        resp = s3_test_obj.object_download(self.bucket_name, self.object_name, download_path)
        LOGGER.info("Download object response: %s", resp)
        assert_utils.assert_true(resp[0], resp[1])
        download_checksum = self.ha_obj.cal_compare_checksum(file_list=[download_path],
                                                             compare=False)[0]
        assert_utils.assert_equal(upload_checksum, download_checksum,
                                  f"Failed to match checksum: {upload_checksum},"
                                  f" {download_checksum}")
        LOGGER.info("Matched checksum: %s, %s", upload_checksum, download_checksum)
        LOGGER.info("Step 7: Successfully downloaded the object and verified the checksum")

        LOGGER.info("ENDED: Test to verify partial multipart upload after each data pod is "
                    "failed till K data pods and complete upload after all K data pods are failed")

    @pytest.mark.ha
    @pytest.mark.lc
    @pytest.mark.tags("TEST-35784")
    def test_mpu_during_kpods_shutdown(self):
        """
        This test tests multipart upload during data pods failure till K pods
        """
        LOGGER.info("STARTED: Test to verify multipart upload during data pods failure till K "
                    "data pods by delete deployment")
        file_size = HA_CFG["5gb_mpu_data"]["file_size"]
        total_parts = self.kvalue * 5 + HA_CFG["5gb_mpu_data"]["total_parts"]
        part_numbers = list(range(1, total_parts + 1))
        self.system_random.shuffle(part_numbers)
        output = Queue()
        parts_etag = list()
        download_file = self.test_file + "_download"
        download_path = os.path.join(self.test_dir_path, download_file)
        event = threading.Event()  # Event to be used to send intimation of pod shutdown

        LOGGER.info("Creating IAM user with name %s", self.s3acc_name)
        resp = self.rest_obj.create_s3_account(acc_name=self.s3acc_name,
                                               email_id=self.s3acc_email,
                                               passwd=S3_CFG["CliConfig"]["s3_account"]["password"])
        assert_utils.assert_true(resp[0], resp[1])
        access_key = resp[1]["access_key"]
        secret_key = resp[1]["secret_key"]
        s3_test_obj = S3TestLib(access_key=access_key, secret_key=secret_key,
                                endpoint_url=S3_CFG["s3_url"])
        s3_mp_test_obj = S3MultipartTestLib(access_key=access_key, secret_key=secret_key,
                                            endpoint_url=S3_CFG["s3_url"])
        LOGGER.info("Successfully created IAM user with name %s", self.s3acc_name)
        self.s3_clean = {'s3_acc': {'accesskey': access_key, 'secretkey': secret_key,
                                    'user_name': self.s3acc_name}}

        LOGGER.info("Step 1: Start multipart upload of %s object in background",
                    file_size * const.Sizes.MB)
        args = {'s3_data': self.s3_clean, 'bucket_name': self.bucket_name,
                'object_name': self.object_name, 'file_size': file_size, 'total_parts': total_parts,
                'multipart_obj_path': self.multipart_obj_path, 'part_numbers': part_numbers,
                'parts_etag': parts_etag, 'output': output}
        thread = threading.Thread(target=self.ha_obj.start_random_mpu, args=(event,), kwargs=args)
        thread.daemon = True  # Daemonize thread
        thread.start()
        LOGGER.info("Step 1: Started multipart upload of %s object in background",
                    file_size * const.Sizes.MB)
        time.sleep(HA_CFG["common_params"]["60sec_delay"])

        LOGGER.info("Step 2: Shutdown random %s (K) data pods by deleting deployment while "
                    "continuous multipart upload in background and verify cluster & remaining "
                    "pods status", self.kvalue)
        resp = self.ha_obj.delete_kpod_with_shutdown_methods(
            master_node_obj=self.node_master_list[0], health_obj=self.hlth_master_list[0],
            down_method=const.RESTORE_DEPLOYMENT_K8S, kvalue=self.kvalue)
        # Assert if empty dictionary
        assert_utils.assert_true(resp[1], "Failed to shutdown/delete pod")
        for pod_name in resp[1].keys():
            pod_data = list()
            pod_data.append(resp[1][pod_name]['hostname'])  # hostname
            pod_data.append(resp[1][pod_name]['deployment_backup'])  # deployment_backup
            pod_data.append(resp[1][pod_name]['deployment_name'])  # deployment_name
            self.pod_name_list.append(pod_name)
            self.pod_dict[pod_name] = pod_data
        self.restore_pod = self.deploy = True
        self.restore_method = const.RESTORE_DEPLOYMENT_K8S
        assert_utils.assert_true(resp[0], "Cluster/Services status is not as expected")
        LOGGER.info("Step 2: Successfully shutdown %s (K) data pod %s. Verified cluster and "
                    "services states are as expected & remaining pods status is online.",
                    self.kvalue, self.pod_name_list)

        LOGGER.info("Step 3: Checking response from background process")
        thread.join()
        responses = tuple()
        while len(responses) < 4:
            responses = output.get(timeout=HA_CFG["common_params"]["60sec_delay"])
        if not responses:
            assert_utils.assert_true(False, "Background process failed to do multipart upload")
        exp_failed_parts = responses[0]
        failed_parts = responses[1]
        parts_etag = responses[2]
        mpu_id = responses[3]
        LOGGER.debug("Responses received from background process:\nexp_failed_parts: "
                     "%s\nfailed_parts: %s\nparts_etag: %s\nmpu_id: %s", exp_failed_parts,
                     failed_parts, parts_etag, mpu_id)
        if len(exp_failed_parts) == 0 and len(failed_parts) == 0:
            LOGGER.info("All the parts are uploaded successfully")
        elif failed_parts:
            assert_utils.assert_true(False,
                                     "Failed to upload parts when cluster was in degraded state."
                                     f"Failed parts: {failed_parts}")
        elif exp_failed_parts:
            LOGGER.info("Step 3.1: Upload expected failed remaining parts")
            resp = self.ha_obj.partial_multipart_upload(s3_data=self.s3_clean,
                                                        bucket_name=self.bucket_name,
                                                        object_name=self.object_name,
                                                        part_numbers=exp_failed_parts,
                                                        remaining_upload=True,
                                                        multipart_obj_size=file_size,
                                                        total_parts=total_parts,
                                                        multipart_obj_path=self.multipart_obj_path,
                                                        mpu_id=mpu_id)
            assert_utils.assert_true(resp[0],
                                     f"Failed to upload expected failed remaining parts {resp[1]}")
            parts_etag1 = resp[3]
            parts_etag = parts_etag + parts_etag1
            LOGGER.info("Step 3.1: Successfully uploaded expected failed remaining parts")
        LOGGER.info("Step 3: Successfully checked background process responses")

        parts_etag = sorted(parts_etag, key=lambda d: d['PartNumber'])

        LOGGER.info("Calculating checksum of file %s", self.multipart_obj_path)
        upload_checksum = self.ha_obj.cal_compare_checksum(file_list=[self.multipart_obj_path],
                                                           compare=False)[0]

        LOGGER.info("Step 4: Listing parts of multipart upload")
        res = s3_mp_test_obj.list_parts(mpu_id, self.bucket_name, self.object_name)
        assert_utils.assert_true(res[0], res)
        assert_utils.assert_equal(len(res[1]["Parts"]), total_parts)
        LOGGER.info("Step 4: Listed parts of multipart upload. Count: %s", len(res[1]["Parts"]))

        LOGGER.info("Step 5: Completing multipart upload and check upload size is %s",
                    file_size * const.Sizes.MB)
        res = s3_mp_test_obj.complete_multipart_upload(mpu_id, parts_etag, self.bucket_name,
                                                       self.object_name)
        assert_utils.assert_true(res[0], res)
        res = s3_test_obj.object_list(self.bucket_name)
        assert_utils.assert_in(self.object_name, res[1], res)
        result = s3_test_obj.object_info(self.bucket_name, self.object_name)
        obj_size = result[1]["ContentLength"]
        LOGGER.debug("Uploaded object info for %s is %s", self.bucket_name, result)
        assert_utils.assert_equal(obj_size, file_size * const.Sizes.MB)
        LOGGER.info("Step 5: Multipart upload completed and verified upload object size is %s",
                    obj_size)

        LOGGER.info("Step 6: Download the uploaded object and verify checksum")
        resp = s3_test_obj.object_download(self.bucket_name, self.object_name, download_path)
        LOGGER.info("Download object response: %s", resp)
        assert_utils.assert_true(resp[0], resp[1])
        download_checksum = self.ha_obj.cal_compare_checksum(file_list=[download_path],
                                                             compare=False)[0]
        assert_utils.assert_equal(upload_checksum, download_checksum,
                                  f"Failed to match checksum: {upload_checksum},"
                                  f" {download_checksum}")
        LOGGER.info("Matched checksum: %s, %s", upload_checksum, download_checksum)
        LOGGER.info("Step 6: Successfully downloaded the object and verified the checksum")

        LOGGER.info("COMPLETED: Test to verify multipart upload during data pods failure till K "
                    "pods by delete deployment")