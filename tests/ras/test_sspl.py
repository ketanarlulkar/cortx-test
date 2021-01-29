#!/usr/bin/python
# -*- coding: utf-8 -*-

"""SSPL test cases: Primary Node."""

import os
import time
import random
import logging
import pytest
from libs.ras.ras_test_lib import RASTestLib
from commons.helpers.node_helper import Node
from commons.helpers.health_helper import Health
from commons.helpers.controller_helper import ControllerLib
from commons.helpers.s3_helper import S3Helper
from commons import constants as cons
from commons import commands as common_cmd
from commons.utils import config_utils as conf_util
from commons.utils.assert_utils import *
from libs.csm.rest.csm_rest_alert import SystemAlerts
from libs.csm.rest.csm_rest_csmuser import RestCsmUser
from commons.alerts_simulator.generate_alert_lib import \
     GenerateAlertLib, AlertType

CSM_ALERT_OBJ = SystemAlerts()
CSM_USER_OBJ = RestCsmUser()
ALERT_API_OBJ = GenerateAlertLib()

RAS_TEST_CFG = conf_util.read_yaml(cons.SSPL_TEST_CONFIG_PATH)[1]
COMMON_CONF = conf_util.read_yaml(cons.COMMON_CONFIG_PATH)[1]
RAS_VAL = conf_util.read_yaml(cons.RAS_CONFIG_PATH)[1]
BYTES_TO_READ = cons.BYTES_TO_READ
CM_CFG = RAS_VAL["ras_sspl_alert"]
LOGGER = logging.getLogger(__name__)

TEST_DATA = [COMMON_CONF["host"]]


class SSPLTest:
    """SSPL Test Suite."""

    @pytest.mark.parametrize("host", TEST_DATA)
    def setup_module(self, host):
        """Setup for module."""
        self.host = host
        self.uname = COMMON_CONF["username"]
        self.passwd = COMMON_CONF["password"]
        self.changed_level = False
        self.default_cpu_usage = True
        self.default_mem_usage = True
        self.selinux_enabled = False

        self.ras_test_obj = RASTestLib(host=self.host, username=self.uname,
                                       password=self.passwd)
        self.node_obj = Node(hostname=self.host, username=self.uname,
                             password=self.passwd)
        self.health_obj = Health(hostname=self.host, username=self.uname,
                                 password=self.passwd)
        self.controller_obj = ControllerLib(
            host=self.host, h_user=self.uname, h_pwd=self.passwd,
            enclosure_ip=COMMON_CONF["primary_enclosure_ip"],
            enclosure_user=COMMON_CONF["enclosure_user"],
            enclosure_pwd=COMMON_CONF["enclosure_pwd"])
        try:
            self.s3obj = S3Helper()
        except ImportError as err:
            LOGGER.info(str(err))
            self.s3obj = S3Helper.get_instance()

        # Enable this flag for starting RMQ channel
        self.start_rmq = CM_CFG["start_rmq"]

        field_list = ["primary_controller_ip", "secondary_controller_ip",
                      "primary_controller_port", "secondary_controller_port",
                      "user", "password", "secret"]
        LOGGER.info("Putting expected values in KV store")
        for field in field_list:
            res = self.ras_test_obj.put_kv_store(COMMON_CONF["enclosure_user"],
                                                 COMMON_CONF["enclosure_pwd"],
                                                 field)
            assert res is True

    def setup_function(self):
        """Setup operations."""
        self.starttime = time.time()
        LOGGER.info("Retaining the original/default config")
        self.ras_test_obj.retain_config(CM_CFG["file"]["original_sspl_conf"],
                                        False)

        LOGGER.info("Performing Setup operations")

        LOGGER.info("Checking SSPL state file")
        res = self.ras_test_obj.get_sspl_state()
        if not res:
            LOGGER.info("SSPL not present updating same on server")
            response = self.ras_test_obj.check_status_file()
            assert response[0] is True, response[1]
        LOGGER.info("Done Checking SSPL state file")

        LOGGER.info("Delete keys with prefix SSPL_")
        cmd = common_cmd.REMOVE_UNWANTED_CONSUL
        response = self.node_obj.execute_cmd(cmd=cmd,
                                             read_nbytes=BYTES_TO_READ)
        assert response[0] is True, response[1]

        LOGGER.info("Restarting sspl service")
        self.health_obj.restart_pcs_resource(CM_CFG["sspl_resource_id"])
        time.sleep(CM_CFG["after_service_restart_sleep_val"])
        LOGGER.info(
            "Verifying the status of sspl and rabittmq service is online")

        # Getting SSPl and RabbitMQ service status
        services = CM_CFG["service"]
        for service in services:
            resp = self.s3obj.get_s3server_service_status(
                service=service, host=self.host, user=self.uname,
                pwd=self.passwd)
            assert resp is True

        LOGGER.info(
            "Validated the status of sspl and rabittmq service are online")

        if self.start_rmq:
            LOGGER.info("Running rabbitmq_reader.py script on node")
            resp = self.ras_test_obj.start_rabbitmq_reader_cmd(
                CM_CFG["sspl_exch"], CM_CFG["sspl_key"])
            assert resp is True, "Failed to start RMQ channel"
            LOGGER.info(
                "Successfully started rabbitmq_reader.py script on node")

        LOGGER.info("Starting collection of sspl.log")
        res = self.ras_test_obj.sspl_log_collect()
        assert res[0] is True, res[1]
        LOGGER.info("Started collection of sspl logs")

        LOGGER.info("Successfully performed Setup operations")

    def teardown_function(self):
        """Teardown operations."""
        LOGGER.info("Performing Teardown operation")
        self.ras_test_obj.retain_config(CM_CFG["file"]["original_sspl_conf"],
                                        True)

        LOGGER.info("Restoring values to default in consul")
        LOGGER.info("Updating disk usage threshold value")
        res = self.ras_test_obj.update_threshold_values(
            cons.KV_STORE_DISK_USAGE, CM_CFG["sspl_config"]["sspl_du_key"],
            CM_CFG["sspl_config"]["sspl_du_dval"])
        assert res is True

        if not self.default_cpu_usage:
            LOGGER.info("Updating default cpu usage threshold value")
            res = self.ras_test_obj.update_threshold_values(
                cons.KV_STORE_DISK_USAGE, cons.CPU_USAGE_KEY,
                CM_CFG["default_cpu_usage"])
            assert res is True

        if not self.default_mem_usage:
            LOGGER.info("Updating default memory usage threshold value")
            res = self.ras_test_obj.update_threshold_values(
                cons.KV_STORE_DISK_USAGE, cons.MEM_USAGE_KEY,
                CM_CFG["default_mem_usage"])
            assert res is True

        if self.changed_level:
            kv_store_path = cons.LOG_STORE_PATH
            common_cfg = RAS_TEST_CFG["ras_sspl_alert"]["sspl_config"]
            res = self.ras_test_obj.update_threshold_values(
                kv_store_path, common_cfg["sspl_log_level_key"],
                common_cfg["sspl_log_dval"],
                update=True)
            assert res is True

        if os.path.exists(CM_CFG["file"]["telnet_xml"]):
            LOGGER.info("Remove telnet file")
            os.remove(CM_CFG["file"]["telnet_xml"])

        if self.node_obj.path_exists(
                RAS_VAL["ras_sspl_alert"]["file"]["disk_usage_temp_file"]):
            LOGGER.info("Remove temp disk usage file")
            self.node_obj.remove_file(
                filename=RAS_VAL["ras_sspl_alert"]["file"]
                ["disk_usage_temp_file"])

        LOGGER.info("Terminating the process of reading sspl.log")
        self.ras_test_obj.kill_remote_process("/sspl/sspl.log")

        LOGGER.debug("Copying contents of sspl.log")
        read_resp = self.node_obj.read_file(
            filename=CM_CFG["file"]["sspl_log_file"],
            local_path=CM_CFG["file"]["sspl_log_file"])
        LOGGER.debug(
            "======================================================")
        LOGGER.debug(read_resp)
        LOGGER.debug(
            "======================================================")

        LOGGER.info(
            "Removing file {}".format(CM_CFG["file"]["sspl_log_file"]))
        self.node_obj.remove_file(filename=CM_CFG["file"]["sspl_log_file"])

        if self.start_rmq:
            LOGGER.info("Terminating the process rabbitmq_reader.py")
            self.ras_test_obj.kill_remote_process("rabbitmq_reader.py")
            files = [CM_CFG["file"]["alert_log_file"],
                     CM_CFG["file"]["extracted_alert_file"],
                     CM_CFG["file"]["screen_log"]]
            for file in files:
                LOGGER.info(f"Removing log file {file} from the Node")
                self.node_obj.remove_file(filename=file)

        self.health_obj.restart_pcs_resource(
            resource=CM_CFG["sspl_resource_id"])
        time.sleep(CM_CFG["sleep_val"])

        if self.selinux_enabled:
            local_path = CM_CFG["local_selinux_path"]
            new_value = CM_CFG["selinux_disabled"]
            old_value = CM_CFG["selinux_enforced"]
            LOGGER.info("Modifying selinux status from {} to {} on node {}"
                        .format(old_value, new_value, self.host))
            resp = self.ras_test_obj.modify_selinux_file()
            assert resp[0] is True, resp[1]
            LOGGER.info(
                "Modified selinux status to {}".format(new_value))

            LOGGER.info(
                "Rebooting node {} after modifying selinux status".format(
                    self.host))
            response = self.node_obj.execute_cmd(cmd=common_cmd.REBOOT_NODE_CMD)

            time.sleep(CM_CFG["reboot_delay"])
            os.remove(local_path)
            LOGGER.info("Rebooted node {} after modifying selinux status"
                        .format(self.host))

        LOGGER.info("Successfully performed Teardown operation")

    @pytest.mark.ras
    @pytest.mark.sw_alert
    @pytest.mark.tags("TEST-9956")
    def test_3005(self):
        """
        EES ras SSPL: Node: Disk Space-Full Alerts #1
        sspl_disk_space_alert
        """
        LOGGER.info("STARTED: TEST-3005: EES ras SSPL: "
                    "Node: Disk Space-Full Alerts #1")
        common_cfg = RAS_VAL["ras_sspl_alert"]
        params = RAS_TEST_CFG["test_3005"]

        LOGGER.info("Step 1: Running ALERT API")
        resp = ALERT_API_OBJ.generate_alert(
            AlertType.disk_fault_no_alert,
            input_parameters={
                "du_val": params["du_val"],
                "fault": False,
                "fault_resolved": False})
        assert resp[0] is True
        LOGGER.info("Step 1: Successfully run ALERT API")

        if self.start_rmq:
            LOGGER.info("Step 2: Checking the generated alert logs")
            alert_list = [params["resource_type"], params["alert_type"]]
            resp = self.ras_test_obj.list_alert_validation(alert_list)
            assert resp[0] is True, resp[1]
            LOGGER.info(
                "Step 2: No alerts are seen for disk threshold greater "
                "than disk usage")

        time.sleep(common_cfg["sleep_val"])
        LOGGER.info("Step 3: Checking CSM REST API for no alerts")
        resp = CSM_ALERT_OBJ.verify_csm_response(self.starttime,
                                                 params["alert_type"], False,
                                                 params["resource_type"])

        assert resp is True, "No alert should be seen in CSM REST API"
        LOGGER.info(
            "Step 3: Successfully checked CSM REST API for no alerts")

        LOGGER.info("ENDED: TEST-3005: EES ras SSPL: "
                    "Node: Disk Space-Full Alerts #1")

    @pytest.mark.ras
    @pytest.mark.sw_alert
    @pytest.mark.tags("TEST-9957")
    def test_3006(self):
        """
        EES ras SSPL: Node: Disk Space-Full Alerts #2
        sspl_disk_space_alert
        """
        LOGGER.info("STARTED:TEST-3006: EES ras SSPL: "
                    "Node: Disk Space-Full Alerts #2")
        common_cfg = RAS_VAL["ras_sspl_alert"]
        params = RAS_TEST_CFG["test_3006"]

        LOGGER.info(
            "Step 1: Running ALERT API for generating and resolving disk "
            "full fault")
        resp = ALERT_API_OBJ.generate_alert(
            AlertType.disk_fault_resolved_alert,
            input_parameters={
                "du_val": params["alert_fault_resolved"]["du_val"],
                "fault": True,
                "fault_resolved": True})
        assert resp[0] is True
        LOGGER.info(
            "Step 1: Successfully run ALERT API for generating and resolving "
            "disk full fault")

        if self.start_rmq:
            LOGGER.info("Step 2: Checking the generated alert logs")
            alert_list = [params["resource_type"],
                          params["alert_fault"]["alert_type"],
                          params["alert_fault_resolved"]["alert_type"]]
            resp = self.ras_test_obj.list_alert_validation(alert_list)
            assert resp[0] is True, resp[1]
            LOGGER.info(
                "Step 2: Verified the generated disk full fault and "
                "fault_resolved alert")

        LOGGER.info("Step 3: Checking CSM REST API for alert type "
                    "fault_resolved")
        resp = CSM_ALERT_OBJ.verify_csm_response(
            self.starttime,
            params["alert_fault_resolved"]["alert_type"],
            True,
            params["resource_type"])

        assert resp is True, common_cfg["csm_error_msg"]
        LOGGER.info("Step 3: Successfully checked CSM REST API for alerts")

        LOGGER.info("ENDED:TEST-3006: EES ras SSPL: "
                    "Node: Disk Space-Full Alerts #2")

    @pytest.mark.ras
    @pytest.mark.sw_alert
    @pytest.mark.tags("TEST-9958")
    def test_3104(self):
        """
        EOS-8135 : Validate EES RAS SSPL: Sync with systemd (to bring down
        startup within SLA
        sspl_startup_time
        """
        LOGGER.info(
            "STARTED: Validate EES RAS SSPL: Sync with systemd "
            "(to bring down startup within SLA")
        service_name = RAS_VAL["ras_sspl_alert"]["sspl_resource_id"]
        test_cfg = RAS_TEST_CFG["test_3104"]
        buffer_sz = test_cfg["buffer_sz"]

        LOGGER.info("Step 1: Restart the SSPL Service")
        resp = self.ras_test_obj.restart_service(service_name)
        assert resp[0] is True, resp[1]
        LOGGER.info("Step 1: Successfully Restarted the SSPL Service")

        LOGGER.info("Step 2: Check the restart time of SSPL service")
        check_time_cmd = test_cfg["check_time_sppl"].format(service_name)
        resp = self.node_obj.execute_cmd(cmd=check_time_cmd,
                                         read_nbytes=buffer_sz)
        assert resp[0] is True, resp[1]
        restart_time = resp[1].strip()
        resp = self.ras_test_obj.validate_exec_time(restart_time.decode(
            "utf-8"))
        assert resp[0] is True, resp[1]
        LOGGER.info("Step 2: Verified the restart time of SSPL service")

        LOGGER.info("ENDED: Validate EES RAS SSPL: Sync with systemd "
                    "(to bring down startup within SLA)")

    @pytest.mark.ras
    @pytest.mark.sw_alert
    @pytest.mark.tags("TEST-9959")
    def test_3161(self):
        """
        EOS-8135 : Validating EOS v1 RAS: Node: IPMI: FAN Failure Alerts
        sspl_fan_alert
        """
        LOGGER.info(
            "STARTED: Validating EOS v1 RAS: Node: IPMI: FAN Failure Alerts")
        common_cfg = RAS_VAL["ras_sspl_alert"]
        test_cfg = RAS_TEST_CFG["test_3161"]
        buffer_sz = test_cfg["buffer_sz"]
        csm_error_msg = RAS_VAL["ras_sspl_alert"]["csm_error_msg"]

        LOGGER.info(
            "Step 1: run 'ipmitool sdr list' to inquire about FAN "
            "state/details")
        fan_name = self.ras_test_obj.get_fan_name(self.host)
        LOGGER.info("Step 1: Received the FAN state/details")

        LOGGER.info(
            "Step 2: Run 'ipmitool sel list' to list the events")
        sel_lst_cmd = common_cmd.SEL_LIST_CMD
        resp = self.node_obj.execute_cmd(cmd=sel_lst_cmd,
                                         read_nbytes=buffer_sz)
        assert resp[0] is True, resp[1]
        old_event_lst = resp[1]
        LOGGER.info(
            "Step 2: Successfully listed all the SEL events")

        LOGGER.info("Step 3: Generate ipmi sel entry for respective "
                    "FAN through below commands")
        for k in test_cfg["ipmitool_event"]:
            ipmitool_cmd = k.format(fan_name)
            resp = self.node_obj.execute_cmd(cmd=ipmitool_cmd,
                                             read_nbytes=buffer_sz)
            LOGGER.info("SEL response : {}".format(resp))
            assert resp[0] is True, resp[1]
            time.sleep(test_cfg["wait_time"])
        LOGGER.info("Step 3: Generated all the ipmitool FAN events")

        LOGGER.info(
            "Step 4: Run below command now to get SEL event list")
        resp = self.node_obj.execute_cmd(cmd=sel_lst_cmd,
                                         read_nbytes=buffer_sz)
        assert resp[0] is True, resp[1]
        new_event_lst = resp[1]
        assert len(old_event_lst) <= len(new_event_lst), new_event_lst
        LOGGER.info("Step 4: Successfully generate all the ipmitool FAN events "
                    "and resp is :{}".format(resp))

        operations = test_cfg["operations"]
        for op in operations:
            LOGGER.info(f"Step 5: Resolving fan fault using ipmi tool using"
                        f" {op}")
            cmd = common_cmd.RESOLVE_FAN_FAULT.format(fan_name, op)
            LOGGER.info(f"Running command: {cmd}")
            resp = self.node_obj.execute_cmd(cmd=cmd,
                                             read_nbytes=test_cfg["buffer_sz"])
            LOGGER.info("SEL response : {}".format(resp))
            assert resp[0] is True, resp[1]
            LOGGER.info("Step 5: Successfully resolved fault on fan {0}"
                        .format(fan_name))

        if self.start_rmq:
            LOGGER.info("Step 6: Check the RabbitMQ channel for no errors")
            LOGGER.info("Checking the generated alert logs")
            resp = self.ras_test_obj.alert_validation(
                test_cfg["alert_type"], test_cfg["resource_type"])
            assert resp[0] is True, resp[1]
            LOGGER.info("Step 6: Successfully verified the RabbitMQ channel "
                        "for no errors")

        LOGGER.info("Step 7: Checking CSM REST API for alerts")
        time.sleep(common_cfg["sleep_val"])
        resp = CSM_ALERT_OBJ.verify_csm_response(self.starttime,
                                                 test_cfg["alert_type"],
                                                 False,
                                                 test_cfg["resource_type"])

        assert resp is True, csm_error_msg
        LOGGER.info("Step 7: Successfully checked CSM REST API for alerts")

        LOGGER.info(
            "ENDED: Validating EOS v1 RAS: Node: IPMI: FAN Failure Alerts")

    @pytest.mark.ras
    @pytest.mark.sw_alert
    @pytest.mark.tags("TEST-9960")
    def test_3280(self):
        """
        EOS-8135 : RAS: Node: IPMI: FAN Failure Alerts Persistent Cache
        sspl_fan_alert
        """
        LOGGER.info("STARTED: RAS: Node: IPMI: FAN Failure Alerts Persistent"
                    " Cache")
        test_cfg = RAS_TEST_CFG["test_3280"]
        common_cfg = RAS_VAL["ras_sspl_alert"]
        buffer_sz = test_cfg["buffer_sz"]
        last_sel_index = cons.LAST_SEL_INDEX
        service_cfg = RAS_VAL["ras_sspl_alert"]["service"]

        LOGGER.info("Step 1: run 'ipmitool sdr list' to inquire about FAN "
                    "state/details")
        fan_name = self.ras_test_obj.get_fan_name(self.host)
        LOGGER.info(f"Step 1: Received the FAN state/details: {fan_name}")

        LOGGER.info("Step 2 and 3: Stop the SSPL service")
        resp = self.ras_test_obj.enable_disable_service(
            "disable", service_cfg["sspl_service"])
        assert resp[0] is False, resp[1]
        LOGGER.info(
            "Step 2 and 3: SSPL service was successfully stopped and validated")
        time.sleep(test_cfg["wait_time"])

        LOGGER.info("Step 4: Check the value of last sel index")
        resp = self.node_obj.execute_cmd(cmd=last_sel_index,
                                         read_nbytes=buffer_sz)
        assert resp[0] is True, resp[1]
        LOGGER.info("SEL cmd response : {}".format(resp))
        prev_sel_index = resp[1].decode("utf-8").strip()
        LOGGER.info("Step 4: The last sel index value resp : {}".
                    format(resp[1]))

        LOGGER.info("Step 5: Generate ipmi sel entry for respective "
                    "FAN through below commands")
        ipmitool_cmd = test_cfg["ipmitool_event"].format(fan_name)
        resp = self.node_obj.execute_cmd(cmd=ipmitool_cmd,
                                         read_nbytes=buffer_sz)
        assert resp[0] is True, resp[1]
        LOGGER.info("SEL cmd response : {}".format(resp))
        LOGGER.info("Step 5: Successfully generated the ipmitool FAN events")

        LOGGER.info(
            "Step 6 and 7: Validate last sel index value which should be same")
        res = self.node_obj.execute_cmd(cmd=last_sel_index,
                                        read_nbytes=buffer_sz)
        assert res[0] is True, res[1]
        curr_sel_index = res[1].decode("utf-8").strip()
        assert str(prev_sel_index) == str(curr_sel_index).strip(), res[1]

        sel_lst_cmd = common_cmd.SEL_LIST_CMD
        resp = self.node_obj.execute_cmd(cmd=sel_lst_cmd,
                                         read_nbytes=buffer_sz)
        assert resp[0] is True, resp[1]
        lst = [sel.strip() for sel in resp[1]]
        index_val = lst[-1].split("|")[0].strip()
        assert index_val != curr_sel_index, index_val
        LOGGER.info(
            "Step 6 and 7: Successfully validated the last sel index "
            "value and resp : {}".format(resp))

        LOGGER.info("Step 8: Restart  the SSPL service")
        resp = self.ras_test_obj.enable_disable_service("enable",
                                                        service_cfg[
                                                            "sspl_service"])
        assert resp[0] is True, resp[1]
        LOGGER.info("Step 8: Successfully started the SSPL service")

        if self.start_rmq:
            LOGGER.info("Step 9: Check the RabbitMQ channel for no errors")
            alert_list = [test_cfg["resource_type"], test_cfg["alert_type"]]
            LOGGER.debug(f"RMQ alert check: {alert_list}")
            resp = self.ras_test_obj.alert_validation(alert_list, False)
            assert resp[0] is True, resp[1]
            LOGGER.info("Step 9: Successfully verified the RabbitMQ channel "
                        "for no errors")

        LOGGER.info("Step 10: Validate the SSL index cache updated or not")
        res = self.node_obj.execute_cmd(cmd=last_sel_index,
                                        read_nbytes=buffer_sz)
        assert res[0] is True, res[1]
        curr_sel_index_after_res = res[1].decode("utf-8").strip()
        assert str(prev_sel_index) != str(curr_sel_index_after_res).strip(), \
            res[1]
        LOGGER.info("Step 10: Successfully validate the SSL index cache and "
                    "resp is : {}".format(res))

        time.sleep(common_cfg["sleep_val"])
        LOGGER.info("Step 11: Checking CSM REST API for alert")
        resp = CSM_ALERT_OBJ.verify_csm_response(self.starttime,
                                                 test_cfg["alert_type"], False,
                                                 test_cfg["resource_type"])

        assert resp is True, common_cfg["csm_error_msg"]
        LOGGER.info("Step 11: Successfully checked CSM REST API for alerts")

        LOGGER.info("Step 12: Resolving fan fault using ipmi tool")
        cmd = common_cmd.RESOLVE_FAN_FAULT.format(fan_name, test_cfg["op"])
        LOGGER.info(f"Running command: {cmd}")
        resp = self.node_obj.execute_cmd(cmd=cmd,
                                         read_nbytes=test_cfg["buffer_sz"])
        LOGGER.info("SEL response : {}".format(resp))
        assert resp[0] is True, resp[1]
        LOGGER.info("Step 12: Successfully resolved fault on fan {0}".
                    format(fan_name))

        LOGGER.info(
            "ENDED: RAS: Node: IPMI: FAN Failure Alerts Persistent "
            "Cache")

    @pytest.mark.ras
    @pytest.mark.sw_alert
    @pytest.mark.tags("TEST-9961")
    def test_1299(self):
        """
        EOS-8135 : Validating EOS v1 RAS: Node: IPMI: FAN Failure Alerts
        sspl_fan_alert
        """
        LOGGER.info(
            "STARTED: Validate EES RAS SSPL: Sync with systemd "
            "(to bring down startup within SLA")
        test_cfg = RAS_TEST_CFG["test_1299"]
        buffer_sz = test_cfg["buffer_sz"]

        LOGGER.info("Step 1: run 'ipmitool sdr list' to inquire about FAN "
                    "state/details")
        fan_name = self.ras_test_obj.get_fan_name(self.host)
        LOGGER.info("Step 1: Received the FAN state/details")

        LOGGER.info("Step 2 and 3: Generate ipmi sel entry for till cache "
                    "space reaches 90 percent")
        ipmitool_cmd = test_cfg["ipmitool_event"].format(fan_name)
        cache_flag = True
        while cache_flag:
            LOGGER.info("Generating multiple FAN alerts for filling the cache "
                        "entry")
            for _ in range(test_cfg["batch_count"]):
                resp = self.node_obj.execute_cmd(cmd=ipmitool_cmd,
                                                 read_nbytes=buffer_sz)
                LOGGER.info("SEL cmd response : {}".format(resp))
                assert resp[0] is True, resp[1]
            cache_per = self.ras_test_obj.cal_sel_space()
            LOGGER.info("Cache percentage usage : {}%".format(cache_per))
            if cache_per >= test_cfg["range_max"]:
                cache_flag = False
        assert cache_flag is False, resp
        LOGGER.info("Step 2 and 3: Validate the cache sel entry up to max")
        # List sel
        LOGGER.info("Step 4: To check the sel entries cleared or not")
        sel_lst_cmd = cons.SEL_LIST_CMD
        resp = self.node_obj.execute_cmd(cmd=sel_lst_cmd,
                                         read_nbytes=buffer_sz)
        assert resp[0] is True, resp[1]
        LOGGER.info("Step 4: Verified the sel list entries and resp : {}".
                    format(resp))

        LOGGER.info("ENDED: Validating EOS v1 RAS: Node: IPMI: FAN Failure "
                    "Alerts")

    @pytest.mark.ras
    @pytest.mark.hw_alert
    @pytest.mark.tags("TEST-10622")
    def test_4332(self):
        """
        EOS-9075: TA RAS Automation: Validate alert for PSU Module Fault/
        cable missing from 5U84 Enclosure.
        sspl_fan_alert
        """
        LOGGER.info(
            "STARTED: EOS-9075: TA RAS Automation: Validate alert for PSU "
            "Module Fault/cable missing from 5U84 Enclosure")

        params = RAS_TEST_CFG["test_4332"]
        csm_error_msg = RAS_VAL["ras_sspl_alert"]["csm_error_msg"]

        LOGGER.info("Step 1: Simulating fault psu state on MC debug console")
        response = ALERT_API_OBJ.generate_alert(AlertType.psu_fault)

        assert response[0] is True, "{} {}".format(response[1],
                                                   "Couldn't connect to port "
                                                   "7900. Please try on other "
                                                   "controller (Generating "
                                                   "Fault)")

        LOGGER.info("Step 1: Successfully simulated fault psu state on MC "
                    "debug console")

        time.sleep(RAS_VAL["ras_sspl_alert"]["telnet_sleep_val"])

        LOGGER.info("Step 2: Checking CSM REST API for alert")
        resp_csm = CSM_ALERT_OBJ.verify_csm_response(self.starttime,
                                                     params["alert_type"],
                                                     False,
                                                     params["resource_type"])

        LOGGER.info("Step 3: Putting in fault-resolved state")
        response = ALERT_API_OBJ.generate_alert(AlertType.psu_fault_resolved)

        assert response[0] is True, "{} {}".format(response[1],
                                                   "Couldn't connect to port "
                                                   "7900. Please try on other "
                                                   "controller (Generating "
                                                   "Fault)")

        LOGGER.info("Step 3: Successfully simulated fault-resolved "
                    "psu state on MC debug console")

        if self.start_rmq:
            LOGGER.info("Step 4: Checking the generated alert logs")
            alert_list = [params["resource_type"], params["alert_type"]]
            LOGGER.debug(f"RMQ alert check: {alert_list}")
            resp = self.ras_test_obj.alert_validation(alert_list, False)
            assert resp[0] is True, resp[1]
            LOGGER.info("Step 4: Verified the generated alert logs")

        assert resp_csm is True, csm_error_msg
        LOGGER.info("Step 2: Successfully checked CSM REST API for alerts")

        LOGGER.info(
            "ENDED: EOS-9075: TA RAS Automation: Validate alert for PSU Module "
            "Fault/cable missing from 5U84 Enclosure.")

    @pytest.mark.ras
    @pytest.mark.hw_alert
    @pytest.mark.tags("TEST-10900")
    def test_4362(self):
        """
        EOS-9074: TEST: From user perspective validate if alerts are displayed
        with right message alerts - controller fault resolved
        Fault-Resolved from 5U84 Enclosure
        sspl_ctrl_alert
        """
        LOGGER.info(
            "STARTED: EOS-9074: TEST: From user perspective validate if alerts "
            "are displayed  with right "
            "message controller fault resolved from 5U84 Enclosure")
        params = RAS_TEST_CFG["test_4362"]
        csm_error_msg = RAS_VAL["ras_sspl_alert"]["csm_error_msg"]
        common_cfg = RAS_VAL["ras_sspl_alert"]

        LOGGER.info("Step 1: Simulating fault on controller using"
                    "MC debug console")
        response = ALERT_API_OBJ.generate_alert(AlertType.controller_fault)

        assert response[0] is True, "{} {}".format(response[1],
                                                   "Couldn't connect to port "
                                                   "7900. Please try on other "
                                                   "controller (Generating "
                                                   "Fault)")

        LOGGER.info("Step 1: Successfully simulated fault on controller "
                    "using MC debug console")

        time.sleep(RAS_VAL["ras_sspl_alert"]["telnet_sleep_val"])

        LOGGER.info("Step 2: Putting in fault-resolved state")
        response = ALERT_API_OBJ.generate_alert(
            AlertType.controller_fault_resolved)

        assert response[0] is True, "{} {}".format(response[1],
                                                   "Couldn't connect to port "
                                                   "7900. Please try on other "
                                                   "controller (Generating "
                                                   "Fault)")

        LOGGER.info("Step 2: Successfully simulated fault-resolved "
                    "on controller using MC debug console")

        if self.start_rmq:
            LOGGER.info("Step 3: Checking the generated alert logs")
            alert_list = [params["resource_type"], params["alert_type"]]
            LOGGER.debug(f"RMQ alert check: {alert_list}")
            resp = self.ras_test_obj.alert_validation(alert_list, False)
            assert resp[0] is True, resp[1]
            LOGGER.info("Step 3: Verified the generated alert logs")

        time.sleep(common_cfg["sleep_val"])
        LOGGER.info("Step 4: Checking CSM REST API for alert")
        resp = CSM_ALERT_OBJ.verify_csm_response(self.starttime,
                                                 params["alert_type"], True,
                                                 params["resource_type"])

        assert resp is True, csm_error_msg
        LOGGER.info("Step 4: Successfully checked CSM REST API for alerts")

        LOGGER.info(
            "ENDED: EOS-9074: TEST: From user perspective validate if alerts "
            "are displayed with right message controller fault resolved from "
            "5U84 Enclosure")

    @pytest.mark.ras
    @pytest.mark.hw_alert
    @pytest.mark.tags("TEST-10623")
    def test_4335(self):
        """
        EOS-9082 : TA RAS Automation: Validate alerts for PSU Module
        Fault-Resolved from 5U84 Enclosure
        sspl_psu_alert
        """
        LOGGER.info(
            "STARTED: EOS-9082: TA RAS Automation: Validate alerts for PSU "
            "Module Fault-Resolved from 5U84 Enclosure")

        common_cfg = RAS_VAL["ras_sspl_alert"]
        params = RAS_TEST_CFG["test_4335"]
        csm_error_msg = RAS_VAL["ras_sspl_alert"]["csm_error_msg"]

        LOGGER.info("Step 1: Simulating fault psu state on MC debug console")
        response = ALERT_API_OBJ.generate_alert(AlertType.psu_fault)

        assert response[0] is True, "{} {}".format(response[1],
                                                   "Couldn't connect to port "
                                                   "7900. Please try on other "
                                                   "controller (Generating "
                                                   "Fault)")

        LOGGER.info("Step 1: Successfully simulated fault psu state on MC "
                    "debug console")

        time.sleep(RAS_VAL["ras_sspl_alert"]["telnet_sleep_val"])

        LOGGER.info("Step 2: Putting in fault-resolved state")
        response = ALERT_API_OBJ.generate_alert(AlertType.psu_fault_resolved)

        assert response[0] is True, "{} {}".format(response[1],
                                                   "Couldn't connect to port "
                                                   "7900. Please try on other "
                                                   "controller (Generating "
                                                   "Fault)")

        LOGGER.info("Step 2: Successfully simulated fault-resolved "
                    "psu state on MC debug console")

        if self.start_rmq:
            LOGGER.info("Step 3: Checking the generated alert logs")
            alert_list = [params["resource_type"], params["alert_type"]]
            LOGGER.debug(f"RMQ alert check: {alert_list}")
            resp = self.ras_test_obj.alert_validation(alert_list, False)
            assert resp[0] is True, resp[1]
            LOGGER.info("Step 3: Verified the generated alert logs")

        time.sleep(common_cfg["sleep_val"])
        LOGGER.info("Step 4: Checking CSM REST API for alert")
        resp = CSM_ALERT_OBJ.verify_csm_response(self.starttime,
                                                 params["alert_type"], True,
                                                 params["resource_type"])

        assert resp is True, csm_error_msg
        LOGGER.info("Step 4: Successfully checked CSM REST API for alerts")

        LOGGER.info(
            "ENDED: EOS-9082: TA RAS Automation: Validate alerts for PSU "
            "Module Fault-Resolved from 5U84 Enclosure ")

    @pytest.mark.ras
    @pytest.mark.hw_alert
    @pytest.mark.tags("TEST-10624")
    def test_4361(self):
        """
        EOS-9078 : TA RAS Automation: Validate if alerts are displayed with
        right message - controller faulted
        sspl_ctrl_alert
        """
        LOGGER.info(
            "STARTED: EOS-9078: TA RAS Automation: Validate if alerts are "
            "displayed with right message - controller faulted")

        params = RAS_TEST_CFG["test_4361"]
        csm_error_msg = RAS_VAL["ras_sspl_alert"]["csm_error_msg"]

        LOGGER.info("Step 1: Simulating fault on controller using"
                    "MC debug console")
        response = ALERT_API_OBJ.generate_alert(AlertType.controller_fault)

        assert response[0] is True, "{} {}".format(response[1],
                                                   "Couldn't connect to port "
                                                   "7900. Please try on other "
                                                   "controller (Generating "
                                                   "Fault)")

        LOGGER.info("Step 1: Successfully simulated fault on controller "
                    "using MC debug console")

        time.sleep(RAS_VAL["ras_sspl_alert"]["telnet_sleep_val"])

        LOGGER.info("Step 2: Checking CSM REST API for alert")
        resp_csm = CSM_ALERT_OBJ.verify_csm_response(self.starttime,
                                                     params["alert_type"],
                                                     False,
                                                     params["resource_type"])

        LOGGER.info("Step 3: Putting in fault-resolved state")
        response = ALERT_API_OBJ.generate_alert(
            AlertType.controller_fault_resolved)

        assert response[0] is True, "{} {}".format(response[1],
                                                   "Couldn't connect to port "
                                                   "7900. Please try on other "
                                                   "controller (Generating "
                                                   "Fault)")

        LOGGER.info("Step 3: Successfully simulated fault-resolved "
                    "on controller using MC debug console")

        if self.start_rmq:
            LOGGER.info("Step 4: Checking the generated alert logs")
            alert_list = [params["resource_type"], params["alert_type"]]
            LOGGER.debug(f"RMQ alert check: {alert_list}")
            resp = self.ras_test_obj.alert_validation(alert_list, False)
            assert resp[0] is True, resp[1]
            LOGGER.info("Step 4: Verified the generated alert logs")

        assert resp_csm is True, csm_error_msg
        LOGGER.info("Step 2: Successfully checked CSM REST API for alerts")

        LOGGER.info(
            "ENDED: EOS-9078: TA RAS Automation: Validate if alerts are "
            "displayed with right message - controller faulted")

    @pytest.mark.ras
    @pytest.mark.sw_alert
    @pytest.mark.tags("TEST-11225")
    def test_6916(self):
        """
         EOS-9865 : Validating EES RAS: Allow log level setting is not changed
         when after restarting the SSPL service
         sspl_log_level
         """
        LOGGER.info("STARTED: Validating EES RAS: Allow log level setting is "
                    "not changed when after restarting the SSPL service")
        common_cfg = RAS_VAL["ras_sspl_alert"]
        test_cfg = RAS_TEST_CFG["test_6916"]
        kv_store_path = cons.LOG_STORE_PATH
        log_level_val = test_cfg["log_level_val"]
        LOGGER.info("Step 1: Ensure SSPL service is up and running")
        resp = self.s3obj.get_s3server_service_status(
            service=common_cfg["service"]["sspl_service"], host=self.host,
            user=self.uname, pwd=self.passwd)
        assert resp[0] is True, resp[1]
        LOGGER.info("Step 1: SSPL service is running")
        LOGGER.info("Step 2: Set the log_level from *INFO to WARNING")
        res = self.ras_test_obj.update_threshold_values(kv_store_path,
                                                        test_cfg["key"],
                                                        log_level_val,
                                                        update=True)
        assert res is True
        self.changed_level = True
        LOGGER.info("Step 2: Successfully set the log_level to WARNING")
        LOGGER.info("Step 3: Restart the SSPL Service")
        resp = self.ras_test_obj.restart_service(
            common_cfg["sspl_resource_id"])

        assert resp[0] is True, resp[1]
        LOGGER.info("Step 3: Successfully Restarted the SSPL Service")
        time.sleep(common_cfg["after_service_restart_sleep_val"])
        LOGGER.info("Step 4: Verify that log_level wont be changed after "
                    "restarting the sspl service")
        res = self.ras_test_obj.update_threshold_values(kv_store_path,
                                                        test_cfg["key"],
                                                        test_cfg[
                                                            "log_level_val"],
                                                        update=False)
        assert res is True
        LOGGER.info("Step 4: Successfully verified the log_level")

        LOGGER.info("ENDED: Validating EES RAS: Allow log level setting is not "
                    "changed when after restarting the SSPL service")

    @pytest.mark.ras
    @pytest.mark.sw_alert
    @pytest.mark.tags("TEST-11224")
    def test_4349(self):
        """
        EOS-9877 : TA RAS Automation: Test scenarios for validating EES RAS:
        Run SSPL on port 5100
        sspl_disk_space_alert
        """
        LOGGER.info(
            "STARTED: TA RAS Automation: Test scenarios for validating EES RAS:"
            " Run SSPL on port 5100")

        common_cfg = RAS_VAL["ras_sspl_alert"]
        csm_error_msg = common_cfg["csm_error_msg"]
        test_cfg = RAS_TEST_CFG["test_4349"]

        LOGGER.info("Step 1: Checking status of sspl services")
        services = common_cfg["service"]
        for service in services:
            resp = self.s3obj.get_s3server_service_status(
                service=common_cfg["service"][service], host=self.host,
                user=self.uname, pwd=self.passwd)
            assert resp[0] is True, resp[1]
            LOGGER.info(f"{service} service is up and running")

        LOGGER.info("Step 2: Updating sspl.log log level to WARNING")
        res = self.ras_test_obj.update_threshold_values(
            cons.KV_STORE_LOG_LEVEL,
            common_cfg["sspl_config"]["sspl_log_level_key"],
            common_cfg["sspl_config"]["sspl_log_level_val"])
        assert res is True
        LOGGER.info("Step 2: Updated sspl.log log level to WARNING")
        self.changed_level = True

        LOGGER.info("Step 3: Collecting logs from sspl.log file")
        cmd = cons.CHECK_SSPL_LOG_FILE.format(test_cfg["test_sspl_file"])
        response = self.node_obj.execute_cmd(cmd=cmd,
                                             read_nbytes=cons.BYTES_TO_READ)
        assert response[0] is True, response[1]
        LOGGER.info("Step 3: Started collection of sspl logs")

        LOGGER.info("Step 4: Updating the port numbers to 5100")
        ports = test_cfg["port_name"]
        for port in ports:
            res = self.ras_test_obj.update_threshold_values(
                cons.KV_STORE_PATH, port, test_cfg["port_number"])
            assert res is True
        LOGGER.info("Step 4: Updated port numbers to 5100")

        LOGGER.info("Step 5: Checking status of sspl services")
        for service in services:
            resp = self.s3obj.get_s3server_service_status(
                service=common_cfg["service"], host=self.host,
                user=self.uname, pwd=self.passwd)
            assert resp[0] is True, resp[1]
            LOGGER.info(f"{service} service is up and running")

        params = RAS_TEST_CFG["test_3006"]
        LOGGER.info("Step 6: Fetching server disk usage")
        resp = self.node_obj.disk_usage_python_interpreter_cmd(
            dir_path=common_cfg["sspl_config"]["server_du_path"])
        assert resp[0] is True, resp[1][0]
        LOGGER.info("Step 6: Fetched server disk usage")
        original_disk_usage = float(resp[1][0])
        LOGGER.info(
            f"Current disk usage of EES server :{original_disk_usage}")

        # Converting value of disk usage to int to update it in sspl.conf
        disk_usage = original_disk_usage + params["alert_fault"]["du_val"]

        LOGGER.info(f"Step 7: Setting value of disk_usage_threshold to value"
                    f" {disk_usage}")

        LOGGER.info("Generating disk full alert")

        LOGGER.info("Step 8: Running ALERT API for generating fault")
        resp = ALERT_API_OBJ.generate_alert(
            AlertType.disk_fault_alert,
            input_parameters={
                "du_val": params["alert_fault"]["du_val"],
                "fault": True,
                "fault_resolved": False})
        assert resp[0] is True, resp
        LOGGER.info(
            "Step 8: Successfully run ALERT API for generating fault")

        current_disk_usage = resp[1]
        if self.start_rmq:
            LOGGER.info("Step 9: Checking the generated alert logs on RMQ "
                        "channel")
            alert_list = [params["resource_type"],
                          params["alert_fault"]["alert_type"]]
            LOGGER.debug(f"RMQ alert check: {alert_list}")
            resp = self.ras_test_obj.alert_validation(alert_list)
            assert resp[0] is True, resp[1]
            LOGGER.info("Step 9: Verified the generated alert logs")

        LOGGER.info("Step 10: Checking logs in sspl.log")
        exp_string = r"WARNING Disk usage increased to \d{2}.\d%, beyond " \
                     r"configured threshold of \d{2}.\d%"

        self.ras_test_obj.check_sspl_log(exp_string, test_cfg["test_sspl_file"])

        time.sleep(common_cfg["sleep_val"])
        LOGGER.info("Step 11: Checking CSM REST API for alert")
        resp = CSM_ALERT_OBJ.verify_csm_response(
            self.starttime,
            params["alert_fault"]["alert_type"],
            False,
            params["resource_type"])

        assert resp is True, csm_error_msg
        LOGGER.info("Step 11: Successfully checked CSM REST API for alerts")

        LOGGER.info(
            "ENDED: TA RAS Automation: Test scenarios for validating EES RAS:"
            " Run SSPL on port 5100")

    @pytest.mark.ras
    @pytest.mark.sw_alert
    @pytest.mark.tags("TEST-12014")
    def test_3424(self):
        """
        EOS-9879 : TA RAS Automation : Sensor to read IEM from syslog
        sspl_iem_alert
        """
        LOGGER.info(
            "STARTED: TA RAS Automation : Sensor to read IEM from syslog")

        common_cfg = RAS_VAL["ras_sspl_alert"]
        csm_error_msg = common_cfg["csm_error_msg"]
        test_cfg = RAS_TEST_CFG["test_3424"]

        LOGGER.info("Step 1: Checking status of rsyslog services")
        service = test_cfg["rsyslog_service"]
        resp = self.s3obj.get_s3server_service_status(
            service=service, host=self.host, user=self.uname, pwd=self.passwd)
        assert resp[0] is True, resp[1]
        LOGGER.info(f"Step 1: {service} service is up and running")

        cmd = test_cfg["logger_cmd"]
        LOGGER.info(f"Step 2: Running command {cmd}")
        response = self.node_obj.execute_cmd(cmd=cmd,
                                             read_nbytes=cons.BYTES_TO_READ)

        assert response[0] is True, response[1]
        LOGGER.info(f"Step 2: Successfully ran command {cmd}")

        if self.start_rmq:
            LOGGER.info("Step 3: Checking the generated alert logs")
            alert_list = [test_cfg["resource_type"], test_cfg["alert_type"]]
            LOGGER.debug(f"RMQ alert check: {alert_list}")
            resp = self.ras_test_obj.alert_validation(alert_list, False)
            assert resp[0] is True, resp[1]
            LOGGER.info(
                "Step 3: Successfully checked the generated alert logs")

            LOGGER.info("Step 4: Checking alert description")
            string = test_cfg["description"]
            resp = self.node_obj.is_string_in_remote_file(
                string=string, file_path=common_cfg["file"]["alert_log_file"])

            assert resp[0] is True, "{} : {}".format(resp[1], string)
            LOGGER.info(f"Step 4: Description of generated alert is : {string}")

        time.sleep(common_cfg["sleep_val"])
        LOGGER.info("Step 5: Checking CSM REST API for alert")
        resp = CSM_ALERT_OBJ.verify_csm_response(self.starttime,
                                                 test_cfg["alert_type"], False,
                                                 test_cfg["resource_type"])

        assert resp is True, csm_error_msg
        LOGGER.info("Step 5: Successfully checked CSM REST API for alerts")

        LOGGER.info("ENDED: TA RAS Automation : Sensor to read IEM from syslog")

    @pytest.mark.ras
    @pytest.mark.sw_alert
    @pytest.mark.tags("TEST-11760")
    def test_6592(self):
        """
         EOS-9870: Validating EES RAS: Allow log level setting dynamically
         sspl_log_level
         """
        LOGGER.info(
            "STARTED: Test QA :Validating EES RAS: Allow log level setting "
            "dynamically")
        common_cfg = RAS_VAL["ras_sspl_alert"]
        test_cfg = RAS_TEST_CFG["test_6592"]
        kv_store_path = cons.LOG_STORE_PATH
        log_level_val = test_cfg["log_level_val"][0]
        log_level_val_lst = test_cfg["log_level_val"]
        LOGGER.info("Step 1: Ensure SSPL service is up and running")
        resp = self.s3obj.get_s3server_service_status(
            service=common_cfg["service"]["sspl_service"], host=self.host,
            user=self.uname, pwd=self.passwd)
        assert resp[0] is True, resp[1]
        LOGGER.info("Step 1: SSPL service is running")
        LOGGER.info("Step 2: set the log_level from *INFO to WARNING")
        res = self.ras_test_obj.update_threshold_values(kv_store_path,
                                                        test_cfg["key"],
                                                        log_level_val,
                                                        update=True)
        assert res is True
        LOGGER.info("Step 2: Successfully set the log_level to WARNING")

        LOGGER.info("Step 3: Collecting logs from sspl.log file")
        cmd = cons.CHECK_SSPL_LOG_FILE.format(test_cfg["test_sspl_file"])
        response = self.node_obj.execute_cmd(cmd=cmd,
                                             read_nbytes=cons.BYTES_TO_READ)
        assert response[0] is True, response[1]
        LOGGER.info("Step 3: Started collection of sspl logs")

        LOGGER.info("Step 4: Verify the warning and error in the log file")
        res = self.ras_test_obj.verify_the_logs(test_cfg["test_sspl_file"],
                                                log_level_val_lst)
        assert res is True
        LOGGER.info("Step 4: Verified the warning log message in the log "
                    "file")

        LOGGER.info(
            "ENDED: Validating EES RAS: Allow log level setting "
            "dynamically")

    @pytest.mark.ras
    @pytest.mark.hw_alert
    @pytest.mark.tags("TEST-11762")
    def test_157(self):
        """
        EOS-9962: TA RAS Automation: Test Disabling a drive from disk group
        sspl_disk_alert
        """
        LOGGER.info("STARTED: TA RAS Automation: Test Disabling a drive from "
                    "disk group")

        common_cfg = RAS_VAL["ras_sspl_alert"]
        csm_error_msg = common_cfg["csm_error_msg"]
        test_cfg = RAS_TEST_CFG["test_157"]

        LOGGER.info("Step 1: Getting total number of drives mapped")
        resp = self.controller_obj.get_total_drive_count(
            telnet_file=common_cfg["file"]["telnet_xml"])

        assert resp[0] is True, resp[1]
        LOGGER.info(f"Step 1: Total number of mapped drives is {resp[1]}")

        LOGGER.info("Randomly picking phy to disable")
        phy_num = random.randint(0, resp[1] - 1)

        LOGGER.info(f"Step 2: Disabling phy number {phy_num}")
        resp = ALERT_API_OBJ.generate_alert(
            AlertType.disk_disable,
            input_parameters={"enclid": test_cfg["encl"],
                              "ctrl_name": test_cfg["ctrl"],
                              "phy_num": phy_num,
                              "operation": test_cfg["operation_fault"],
                              "exp_status": test_cfg["degraded_phy_status"],
                              "telnet_file": common_cfg["file"]["telnet_xml"]})
        assert resp[0] is True, resp[1]

        phy_stat = test_cfg["degraded_phy_status"]
        LOGGER.info(f"Step 2: Successfully put phy in {phy_stat} state")

        if phy_num < 10:
            resource_id = "disk_00.0{}".format(phy_num)
        else:
            resource_id = "disk_00.{}".format(phy_num)
        time.sleep(common_cfg["sleep_val"])

        LOGGER.info("Step 3: Checking CSM REST API for alert")
        time.sleep(common_cfg["csm_alert_gen_delay"])
        resp_csm = CSM_ALERT_OBJ.verify_csm_response(self.starttime,
                                                     test_cfg["alert_type"],
                                                     False,
                                                     test_cfg["resource_type"],
                                                     resource_id)

        LOGGER.info(f"Step 4: Clearing metadata of drive {phy_num}")
        drive_num = f"0.{phy_num}"
        resp = self.controller_obj.clear_drive_metadata(drive_num=drive_num)
        assert resp[0] is True, resp[1]
        LOGGER.info(f"Step 4: Cleared {drive_num} drive metadata "
                    f"successfully")

        LOGGER.info(f"Step 5: Again enabling phy number {phy_num}")
        i = 0
        while i < test_cfg["retry"]:
            resp = ALERT_API_OBJ.generate_alert(
                AlertType.disk_enable,
                input_parameters={"enclid": test_cfg["encl"],
                                  "ctrl_name": test_cfg["ctrl"],
                                  "phy_num": phy_num,
                                  "operation": test_cfg["operation_fault_resolved"],
                                  "exp_status": test_cfg["ok_phy_status"],
                                  "telnet_file": common_cfg["file"]["telnet_xml"]})

            phy_stat = test_cfg["ok_phy_status"]
            if resp[1] == phy_stat:
                break
            elif i == 1:
                assert phy_stat == resp[1], f"Step 4: Failed to put phy in " \
                                            f"{phy_stat} state"

        LOGGER.info(f"Step 5: Successfully put phy in {phy_stat} state")

        if self.start_rmq:
            LOGGER.info("Step 6: Checking the generated alert logs")
            alert_list = [test_cfg["resource_type"], test_cfg["alert_type"],
                          resource_id]
            resp = self.ras_test_obj.list_alert_validation(alert_list)
            assert resp[0] is True, resp[1]
            LOGGER.info("Step 6: Checked generated alert logs")

        assert resp_csm is True, csm_error_msg
        LOGGER.info("Step 3: Successfully checked CSM REST API for alerts")

        LOGGER.info(
            "ENDED: TA RAS Automation: Test Disabling a drive from disk group")

    @pytest.mark.ras
    @pytest.mark.hw_alert
    @pytest.mark.tags("TEST-11763")
    def test_158(self):
        """
        EOS-9963: TA RAS Automation: Test Enabling a drive from disk group
        sspl_disk_alert
        """
        LOGGER.info(
            "STARTED: TA RAS Automation: Test Enabling a drive from disk group")

        common_cfg = RAS_VAL["ras_sspl_alert"]
        csm_error_msg = common_cfg["csm_error_msg"]
        test_cfg = RAS_TEST_CFG["test_158"]

        LOGGER.info("Step 1: Getting total number of drives mapped")
        resp = self.controller_obj.get_total_drive_count(
            telnet_file=common_cfg["file"]["telnet_xml"])

        assert resp[0] is True, resp[1]
        LOGGER.info(f"Step 1: Total number of mapped drives is {resp[1]}")

        LOGGER.info("Randomly picking phy to disable")
        phy_num = random.randint(0, resp[1] - 1)

        LOGGER.info(f"Step 2: Disabling phy number {phy_num}")
        resp = ALERT_API_OBJ.generate_alert(
            AlertType.disk_disable,
            input_parameters={"enclid": test_cfg["encl"],
                              "ctrl_name": test_cfg["ctrl"],
                              "phy_num": phy_num,
                              "operation": test_cfg["operation_fault"],
                              "exp_status": test_cfg["degraded_phy_status"],
                              "telnet_file": common_cfg["file"]["telnet_xml"]})
        assert resp[0] is True, resp[1]

        phy_stat = test_cfg["degraded_phy_status"]
        LOGGER.info(f"Step 2: Successfully put phy in {phy_stat} state")

        LOGGER.info(f"Step 3: Clearing metadata of drive {phy_num}")
        drive_num = f"0.{phy_num}"
        resp = self.controller_obj.clear_drive_metadata(drive_num=drive_num)
        assert resp[0] is True, resp[1]
        LOGGER.info(f"Step 3: Cleared {drive_num} drive metadata "
                    f"successfully")

        LOGGER.info(f"Step 4: Again enabling phy number {phy_num}")
        i = 0
        while i < test_cfg["retry"]:
            resp = ALERT_API_OBJ.generate_alert(
                AlertType.disk_enable,
                input_parameters={"enclid": test_cfg["encl"],
                                  "ctrl_name": test_cfg["ctrl"],
                                  "phy_num": phy_num,
                                  "operation": test_cfg["operation_fault_resolved"],
                                  "exp_status": test_cfg["ok_phy_status"],
                                  "telnet_file": common_cfg["file"]["telnet_xml"]})

            phy_stat = test_cfg["ok_phy_status"]
            if resp[1] == phy_stat:
                break
            elif i == 1:
                assert phy_stat == resp[1], f"Step 3: Failed to put phy in " \
                                            f"{phy_stat} state"

        LOGGER.info(f"Step 4: Successfully put phy in {phy_stat} state")

        if phy_num < 10:
            resource_id = "disk_00.0{}".format(phy_num)
        else:
            resource_id = "disk_00.{}".format(phy_num)

        time.sleep(common_cfg["sleep_val"])
        if self.start_rmq:
            LOGGER.info("Step 5: Checking the generated alert logs")
            alert_list = [test_cfg["resource_type"], test_cfg["alert_type"],
                          resource_id]
            resp = self.ras_test_obj.list_alert_validation(alert_list)
            assert resp[0] is True, resp[1]
            LOGGER.info("Step 5: Checked generated alert logs")

        LOGGER.info("Step 6: Checking CSM REST API for alert")
        time.sleep(common_cfg["csm_alert_gen_delay"])
        resp = CSM_ALERT_OBJ.verify_csm_response(self.starttime,
                                                 test_cfg["alert_type"], True,
                                                 test_cfg["resource_type"],
                                                 resource_id)

        assert resp is True, csm_error_msg
        LOGGER.info("Step 6: Successfully checked CSM REST API for alerts")

        LOGGER.info(
            "ENDED: TA RAS Automation: Test Enabling a drive from disk group")

    @pytest.mark.ras
    @pytest.mark.sw_alert
    @pytest.mark.tags("TEST-11761")
    def test_6335(self):
        """
         EOS-9873: Test Enhanced IEM response through decoded IEC
         sspl_iem_alert
         """
        LOGGER.info(
            "STARTED: Test Enhanced IEM response through decoded IEC "
            "dynamically")
        common_cfg = RAS_VAL["ras_sspl_alert"]
        csm_error_msg = common_cfg["csm_error_msg"]
        test_cfg = RAS_TEST_CFG["test_6335"]

        LOGGER.info(
            "Step 1 and 2: Check if files related to IEC decode exists")
        resp = self.node_obj.list_dir(cons.IEM_DIRECTORY)
        assert resp[0] is True, resp[1]
        for file in test_cfg["file_name"]:
            compare(resp[1], file, sequence_item_check=True)
            # assert file in resp[1]
        LOGGER.info("Step 1: Validated the respective files")

        LOGGER.info(
            "Step 2-3: Run iem log command in order to generate an IEM log")
        ied_code_initial = test_cfg["ied_code_initial"]
        err_msg_lst = []
        for file in test_cfg["file_name"][1:]:
            iec_mapping_file = os.path.join(cons.IEM_DIRECTORY, file)
            read_file_cmd = test_cfg["cat_cmd"].format(iec_mapping_file)
            resp = self.node_obj.execute_cmd(cmd=read_file_cmd,
                                             read_lines=True)
            assert resp[0] is True, resp[1]
            LOGGER.info("Alert generation for : {}".format(file))
            for line in resp[1]:
                line = line.strip().split(",")
                ied_code_str = ied_code_initial.format(line[0], line[2])
                iem_log_cmd_str = common_cmd.IEM_LOGGER_CMD.format(ied_code_str)
                self.ras_test_obj.generate_log_err_alert(iem_log_cmd_str)
                err_msg_lst.append(line[2].strip())
                time.sleep(test_cfg["alert_wait"])

        LOGGER.info("Step 2-3: Successfully executed the IEM logger command")

        time.sleep(test_cfg["wait_time"])
        err_msg_lst.insert(0, test_cfg["resource_type"])

        if self.start_rmq:
            LOGGER.info("Step 4: Checking IEM alert responses on RMQ")
            resp = self.ras_test_obj.list_alert_validation(err_msg_lst)
            assert resp[0] is True, resp[1]

            LOGGER.info(
                "Step 4: Successfully validated the IEM alert responses "
                "on the RabbitMQ channel")

        LOGGER.info("Step 5: Checking CSM REST API for alert")
        resp = CSM_ALERT_OBJ.verify_csm_response(self.starttime,
                                                 test_cfg["alert_type"], False,
                                                 test_cfg["resource_type"])

        assert resp is True, csm_error_msg
        LOGGER.info("Step 5: Successfully checked CSM REST API for alerts")

        LOGGER.info(
            "ENDED: Test Enhanced IEM response through decoded IEC")

    @pytest.mark.ras
    @pytest.mark.tags("TEST-14036")
    def test_5924(self):
        """
         EOS-9875: Test Username/Password Security coverage on consul
         consul_security
         """
        LOGGER.info(
            "STARTED: Test Username/Password Security coverage on consul")
        LOGGER.info("Step 1: Modifying and validating enclosure username to "
                    "'{0}' and password to '{1}'".format(COMMON_CONF["enclosure_user"],
                                                         COMMON_CONF["enclosure_pwd"]))
        test_cfg = RAS_TEST_CFG["test_5924"]
        for field in test_cfg["fields"]:
            res = self.ras_test_obj.put_kv_store(COMMON_CONF["enclosure_user"],
                                                 COMMON_CONF["enclosure_pwd"],
                                                 field)
            assert res is True, f"Failed to update value for {field}"
        LOGGER.info(
            "Step 1: Modified and validated enclosure username and password")
        LOGGER.info(
            "ENDED: Test Username/Password Security coverage on consul")

    @pytest.mark.ras
    @pytest.mark.sw_alert
    @pytest.mark.tags("TEST-14795")
    def test_4354(self):
        """
        EOS-12920: User can view / query EES Nodes (1U Servers) OS health view
        (CPU Usage)
        health_view
        """
        LOGGER.info(
            "STARTED: TEST-4354 User can view / query EES Nodes (1U Servers) "
            "OS health view (CPU Usage)")
        common_cfg = RAS_VAL["ras_sspl_alert"]
        test_cfg = RAS_TEST_CFG["test_4354"]
        csm_error_msg = common_cfg["csm_error_msg"]

        LOGGER.info(
            "Step 1: Running ALERT API for generating CPU usage fault")
        resp = ALERT_API_OBJ.generate_alert(
            AlertType.cpu_usage_alert, input_parameters={
                "delta_cpu_usage": test_cfg["delta_cpu_usage"]})
        assert resp[0] is True, resp[1]
        LOGGER.info("Step 1: Ran ALERT API for generating CPU usage fault")
        self.default_cpu_usage = False

        LOGGER.info("Step 2: Checking CSM REST API for CPU usage alerts")
        time.sleep(common_cfg["csm_alert_gen_delay"])
        resp = CSM_ALERT_OBJ.verify_csm_response(
            self.starttime, test_cfg["alert_type"], False,
            test_cfg["resource_type"])

        assert resp is True, csm_error_msg
        LOGGER.info(
            "Step 2: Successfully verified CPU usage alert using CSM REST API")
        LOGGER.info(
            "ENDED: TEST-4354 User can view / query EES Nodes (1U Servers) OS "
            "health view (CPU Usage)")

    @pytest.mark.ras
    @pytest.mark.sw_alert
    @pytest.mark.tags("TEST-15198")
    def test_4355(self):
        """
        EOS-12921: User can view / query EES Nodes (1U Servers) OS health view
        (Main Memory Usage)
        health_view
        """
        LOGGER.info(
            "STARTED: TEST-4355 User can view / query EES Nodes (1U Servers) "
            "OS health view (Main Memory Usage)")
        common_cfg = RAS_VAL["ras_sspl_alert"]
        test_cfg = RAS_TEST_CFG["test_4355"]
        csm_error_msg = common_cfg["csm_error_msg"]

        LOGGER.info(
            "Step 1: Running ALERT API for generating memory usage fault")
        resp = ALERT_API_OBJ.generate_alert(
            AlertType.mem_usage_alert, input_parameters={
                "delta_mem_usage": test_cfg["delta_mem_usage"]})
        assert resp[0] is True, resp[1]
        LOGGER.info(
            "Step 1: Ran ALERT API for generating memory usage fault")
        self.default_mem_usage = False

        LOGGER.info("Step 2: Checking CSM REST API for memory usage alerts")
        time.sleep(common_cfg["csm_alert_gen_delay"])
        resp = CSM_ALERT_OBJ.verify_csm_response(
            self.starttime, test_cfg["alert_type"], False,
            test_cfg["resource_type"])
        assert resp is True, csm_error_msg
        LOGGER.info(
            "Step 2: Successfully verified memory usage alert using CSM REST "
            "API")
        LOGGER.info(
            "ENDED: TEST-4355 User can view / query EES Nodes (1U Servers) OS "
            "health view (Main Memory Usage)")

    @pytest.mark.ras
    @pytest.mark.sw_alert
    @pytest.mark.tags("TEST-4584")
    def test_4584(self):
        """
        EOS-9876: Test SSPL with SELinux enabled
        health_view
        """
        LOGGER.info(
            "STARTED: TEST-4584 Test SSPL with SELinux enabled")
        common_cfg = RAS_VAL["ras_sspl_alert"]
        params = RAS_TEST_CFG["test_3006"]
        old_value = common_cfg["selinux_disabled"]
        new_value = common_cfg["selinux_enforced"]

        LOGGER.info(f"Step 1: Checking selinux status on node {self.host}")
        resp = self.ras_test_obj.get_string_from_file()
        LOGGER.info(f"SELinux Status: {resp[1]}")
        if not resp[0]:
            LOGGER.info(
                "Step 2: Modifying selinux status from {} to {} on node {}"
                .format(old_value, new_value, self.host))
            resp = self.ras_test_obj.modify_selinux_file()
            assert resp[0] is True, resp[1]
            LOGGER.info(
                "Step 2: Modified selinux status to {}".format(new_value))

            LOGGER.info(
                "Step 3: Rebooting node {} after modifying selinux "
                "status".format(self.host))
            resp = self.node_obj.execute_cmd(cmd=common_cmd.REBOOT_NODE_CMD,
                                             read_lines=True)

            time.sleep(common_cfg["reboot_delay"])
            self.selinux_enabled = True
            LOGGER.info(
                "Step 3: Rebooted node {} after modifying selinux "
                "status".format(self.host))

            LOGGER.info(f"Step 4: Again checking selinux status on node"
                        f" {self.host}")
            resp = self.ras_test_obj.get_string_from_file()
            assert resp[0] is True, resp
            LOGGER.info(f"Step 4: SELinux Status: {resp[1]}")

        LOGGER.info(
            "Step 5: Running ALERT API for generating and resolving disk full "
            "fault")
        resp = ALERT_API_OBJ.generate_alert(
            AlertType.disk_fault_resolved_alert,
            input_parameters={
                "du_val": params["alert_fault_resolved"]["du_val"],
                "fault": True,
                "fault_resolved": True})
        assert resp[0] is True
        LOGGER.info(
            "Step 5: Successfully run ALERT API for generating and resolving "
            "disk full fault")

        LOGGER.info("Step 6: Checking CSM REST API for alert type "
                    "fault_resolved")
        resp = CSM_ALERT_OBJ.verify_csm_response(
            self.starttime,
            params["alert_fault_resolved"]["alert_type"],
            True,
            params["resource_type"])

        assert resp is True, common_cfg["csm_error_msg"]
        LOGGER.info("Step 6: Successfully checked CSM REST API for alerts")

        LOGGER.info("Step 7: Checking the status of sspl service")
        resp = self.s3obj.get_s3server_service_status(
            service=common_cfg["service"]["sspl_service"], host=self.host,
            user=self.uname, pwd=self.passwd)
        assert resp[0] is True, resp[1]
        LOGGER.info(
            "Step 7: Validated the status of sspl service is online")
        LOGGER.info(
            "ENDED: TEST-4584 Test SSPL with SELinux enabled")