#!/usr/bin/env python

#
# Copyright (c) 2019 by VMware, Inc. ("VMware")
# Used Copyright (c) 2018 by Network Device Education Foundation, Inc.
# ("NetDEF") in this file.
#
# Permission to use, copy, modify, and/or distribute this software
# for any purpose with or without fee is hereby granted, provided
# that the above copyright notice and this permission notice appear
# in all copies.
#
# THE SOFTWARE IS PROVIDED "AS IS" AND VMWARE DISCLAIMS ALL WARRANTIES
# WITH REGARD TO THIS SOFTWARE INCLUDING ALL IMPLIED WARRANTIES OF
# MERCHANTABILITY AND FITNESS. IN NO EVENT SHALL VMWARE BE LIABLE FOR
# ANY SPECIAL, DIRECT, INDIRECT, OR CONSEQUENTIAL DAMAGES OR ANY
# DAMAGES WHATSOEVER RESULTING FROM LOSS OF USE, DATA OR PROFITS,
# WHETHER IN AN ACTION OF CONTRACT, NEGLIGENCE OR OTHER TORTIOUS
# ACTION, ARISING OUT OF OR IN CONNECTION WITH THE USE OR PERFORMANCE
# OF THIS SOFTWARE.
#


"""
Following tests are covered to test ecmp functionality on iBGP.
1. Verify bgp fast-convergence functionality
"""
import os
import sys
import time
import json
import pytest
from time import sleep

# Save the Current Working Directory to find configuration files.
CWD = os.path.dirname(os.path.realpath(__file__))
sys.path.append(os.path.join(CWD, "../"))
sys.path.append(os.path.join(CWD, "../../"))

# pylint: disable=C0413
# Import topogen and topotest helpers
from lib.topogen import Topogen, get_topogen
from mininet.topo import Topo

from lib.common_config import (
    start_topology,
    write_test_header,
    write_test_footer,
    verify_rib,
    create_static_routes,
    check_address_types,
    interface_status,
    reset_config_on_routers,
    required_linux_kernel_version,
    shutdown_bringup_interface,
    apply_raw_config,
)
from lib.topolog import logger
from lib.bgp import verify_bgp_convergence, create_router_bgp, clear_bgp
from lib.topojson import build_topo_from_json, build_config_from_json


pytestmark = [pytest.mark.bgpd, pytest.mark.staticd]


# Reading the data from JSON File for topology and configuration creation
jsonFile = "{}/ibgp_ecmp_topo3.json".format(CWD)

try:
    with open(jsonFile, "r") as topoJson:
        topo = json.load(topoJson)
except IOError:
    assert False, "Could not read file {}".format(jsonFile)

# Global variables
NEXT_HOPS = {"ipv4": [], "ipv6": []}
NETWORK = {"ipv4": "192.168.1.10/32", "ipv6": "fd00:0:0:1::10/128"}
NEXT_HOP_IP = {"ipv4": "10.0.0.1", "ipv6": "fd00::1"}
BGP_CONVERGENCE = False


class CreateTopo(Topo):
    """
    Test topology builder.

    * `Topo`: Topology object
    """

    def build(self, *_args, **_opts):
        """Build function."""
        tgen = get_topogen(self)

        # Building topology from json file
        build_topo_from_json(tgen, topo)


def setup_module(mod):
    """
    Sets up the pytest environment.

    * `mod`: module name
    """
    global NEXT_HOPS, INTF_LIST_R3, INTF_LIST_R2, TEST_STATIC
    global ADDR_TYPES

    testsuite_run_time = time.asctime(time.localtime(time.time()))
    logger.info("Testsuite start time: {}".format(testsuite_run_time))
    logger.info("=" * 40)

    logger.info("Running setup_module to create topology")

    # This function initiates the topology build with Topogen...
    tgen = Topogen(CreateTopo, mod.__name__)

    # Starting topology, create tmp files which are loaded to routers
    #  to start deamons and then start routers
    start_topology(tgen)

    # Creating configuration from JSON
    build_config_from_json(tgen, topo)

    # Don't run this test if we have any failure.
    if tgen.routers_have_failure():
        pytest.skip(tgen.errors)

    # Api call verify whether BGP is converged
    ADDR_TYPES = check_address_types()

    BGP_CONVERGENCE = verify_bgp_convergence(tgen, topo)
    assert BGP_CONVERGENCE is True, "setup_module :Failed \n Error:" " {}".format(
        BGP_CONVERGENCE
    )

    # STATIC_ROUTE = True
    logger.info("Running setup_module() done")


def teardown_module():
    """
    Teardown the pytest environment.

    * `mod`: module name
    """

    logger.info("Running teardown_module to delete topology")

    tgen = get_topogen()

    # Stop toplogy and Remove tmp files
    tgen.stop_topology()


def static_or_nw(tgen, topo, tc_name, test_type, dut):

    if test_type == "redist_static":
        input_dict_static = {
            dut: {
                "static_routes": [
                    {"network": NETWORK["ipv4"], "next_hop": NEXT_HOP_IP["ipv4"]},
                    {"network": NETWORK["ipv6"], "next_hop": NEXT_HOP_IP["ipv6"]},
                ]
            }
        }
        logger.info("Configuring static route on router %s", dut)
        result = create_static_routes(tgen, input_dict_static)
        assert result is True, "Testcase {} : Failed \n Error: {}".format(
            tc_name, result
        )

        input_dict_2 = {
            dut: {
                "bgp": {
                    "address_family": {
                        "ipv4": {
                            "unicast": {"redistribute": [{"redist_type": "static"}]}
                        },
                        "ipv6": {
                            "unicast": {"redistribute": [{"redist_type": "static"}]}
                        },
                    }
                }
            }
        }

        logger.info("Configuring redistribute static route on router %s", dut)
        result = create_router_bgp(tgen, topo, input_dict_2)
        assert result is True, "Testcase {} : Failed \n Error: {}".format(
            tc_name, result
        )

    elif test_type == "advertise_nw":
        input_dict_nw = {
            dut: {
                "bgp": {
                    "address_family": {
                        "ipv4": {
                            "unicast": {
                                "advertise_networks": [{"network": NETWORK["ipv4"]}]
                            }
                        },
                        "ipv6": {
                            "unicast": {
                                "advertise_networks": [{"network": NETWORK["ipv6"]}]
                            }
                        },
                    }
                }
            }
        }

        logger.info(
            "Advertising networks %s %s from router %s",
            NETWORK["ipv4"],
            NETWORK["ipv6"],
            dut,
        )
        result = create_router_bgp(tgen, topo, input_dict_nw)
        assert result is True, "Testcase {} : Failed \n Error: {}".format(
            tc_name, result
        )


@pytest.mark.parametrize("test_type", ["redist_static"])
def test_ecmp_fast_convergence(request, test_type):
    """This test is to verify bgp fast-convergence cli functionality"""

    tc_name = request.node.name
    write_test_header(tc_name)
    tgen = get_topogen()

    # Verifying RIB routes
    dut = "r3"
    protocol = "bgp"

    reset_config_on_routers(tgen)
    static_or_nw(tgen, topo, tc_name, test_type, "r2")

    for addr_type in ADDR_TYPES:
        input_dict = {"r3": {"static_routes": [{"network": NETWORK[addr_type]}]}}

        logger.info("Verifying %s routes on r3", addr_type)
        result = verify_rib(
            tgen,
            addr_type,
            dut,
            input_dict,
            protocol=protocol,
        )
        assert result is True, "Testcase {} : Failed \n Error: {}".format(
            tc_name, result
        )

    intf1 = topo["routers"]["r2"]["links"]["r3-link1"]["interface"]
    intf2 = topo["routers"]["r2"]["links"]["r3-link2"]["interface"]

    logger.info("Shutdown one of the link b/w r2 and r3")
    shutdown_bringup_interface(tgen, "r2", intf1, False)

    logger.info("Verify bgp neighbors are still up")
    result = verify_bgp_convergence(tgen, topo)
    assert result is True, "Testcase {} : Failed \n Error: {}".format(tc_name, result)

    logger.info("Shutdown another link b/w r2 and r3")
    shutdown_bringup_interface(tgen, "r2", intf2, False)

    logger.info("Wait for 10 sec and make sure bgp neighbors are still up")
    sleep(10)
    result = verify_bgp_convergence(tgen, topo)
    assert result is True, "Testcase {} : Failed \n Error: {}".format(tc_name, result)

    logger.info("No shut links b/w r2 and r3")
    shutdown_bringup_interface(tgen, "r2", intf1, True)
    shutdown_bringup_interface(tgen, "r2", intf2, True)

    logger.info("Enable bgp fast-convergence cli")
    raw_config = {
       "r2": {
          "raw_config": [
            "router bgp {}".format(topo["routers"]["r2"]["bgp"]["local_as"]),
            "bgp fast-convergence",
          ]
       }
    }
    result = apply_raw_config(tgen, raw_config)
    assert result is True, "Testcase {} : Failed Error: {}".format(tc_name, result)

    logger.info("Shutdown one link b/w r2 and r3")
    shutdown_bringup_interface(tgen, "r2", intf1, False)

    logger.info("Verify bgp neighbors goes down immediately")
    result = verify_bgp_convergence(tgen, topo, dut="r2", expected=False)
    assert result is not True, "Testcase {} : Failed \n Error: {}".format(
        tc_name, result
    )

    logger.info("Shutdown second link b/w r2 and r3")
    shutdown_bringup_interface(tgen, "r2", intf2, False)

    logger.info("Verify bgp neighbors goes down immediately")
    result = verify_bgp_convergence(tgen, topo, dut="r2", expected=False)
    assert result is not True, "Testcase {} : Failed \n Error: {}".format(
        tc_name, result
    )

    write_test_footer(tc_name)


if __name__ == "__main__":
    args = ["-s"] + sys.argv[1:]
    sys.exit(pytest.main(args))
