import json
import re
from time import sleep

from cli.ceph.ceph import Ceph
from cli.exceptions import ConfigError, OperationFailedError
from tests.nfs.nfs_operations import cleanup_cluster, setup_nfs_cluster
from tests.nfs.test_nfs_qos_on_cluster_level_enablement import (
    capture_copy_details,
    enable_disable_qos_for_cluster,
)
from utility.log import Log

log = Log(__name__)


def enable_disable_qos_for_export(
    enable_flag,
    ceph_export_nfs_obj,
    cluster_name,
    qos_type=None,
    nfs_name=str,
    export=str,
    **qos_parameters,
):
    # Common validation
    if enable_flag and not qos_type:
        raise ValueError("qos_type is required when enabling QoS")

    operation_key = "enable" if enable_flag else "disable"

    try:
        if enable_flag:
            if qos_type == "PerShare":
                ceph_export_nfs_obj.qos.enable_per_share(
                    nfs_name=nfs_name,
                    export=export,
                    max_export_read_bw=qos_parameters.get("max_export_read_bw"),
                    max_export_write_bw=qos_parameters.get("max_export_write_bw"),
                )
            elif qos_type == "PerClient":
                ceph_export_nfs_obj.qos.enable_per_client(
                    nfs_name=nfs_name,
                    export=export,
                    max_client_read_bw=qos_parameters.get("max_client_read_bw"),
                    max_client_write_bw=qos_parameters.get("max_client_write_bw"),
                )
            elif qos_type == "PerShare_PerClient":
                ceph_export_nfs_obj.qos.enable_per_share_per_client(
                    nfs_name=nfs_name,
                    export=export,
                    max_export_read_bw=qos_parameters.get("max_export_read_bw"),
                    max_export_write_bw=qos_parameters.get("max_export_write_bw"),
                    max_client_read_bw=qos_parameters.get("max_client_read_bw"),
                    max_client_write_bw=qos_parameters.get("max_client_write_bw"),
                )
        else:
            ceph_export_nfs_obj.qos.disable(cluster_id=nfs_name, export=export)
        qos_data = ceph_export_nfs_obj.qos.get(nfs_name=nfs_name, export=export)
        log.info(
            "QoS {0} {1} for export {2} in cluster {3}".format(
                qos_data, operation_key, export, cluster_name
            )
        )
    except Exception as e:
        raise OperationFailedError(
            "Failed to {0} QoS for export {1} in cluster {2} : {3}".format(
                operation_key, export, cluster_name, str(e)
            )
        )


def verify_bw_speed(client, nfs_mount, qos_type, export_bw):
    speed = capture_copy_details(client, nfs_mount, "sample.txt")
    log.info(
        "Transfer speed is {0} for QoS {1} enabled in export level".format(
            speed, qos_type
        )
    )
    if float(re.findall(r"\d+", export_bw["max_export_write_bw"])[0]) >= float(
        re.findall(r"\d+\.\d+", speed)[0]
    ):
        log.info(
            "Test passed: QoS {0} enabled successfully in export level transfer speed is {1}"
            " and max_export_write_bw is {2}".format(
                qos_type, speed, export_bw["max_export_write_bw"]
            )
        )
    else:
        raise OperationFailedError(
            "Test failed: QoS {0} enabled successfully in export level".format(qos_type)
        )


def run(ceph_cluster, **kw):
    """Verify QoS operations on NFS cluster"""
    config = kw.get("config")
    clients = ceph_cluster.get_nodes("client")
    nfs_nodes = ceph_cluster.get_nodes("nfs")
    cluster_name = config["cluster_name"]
    port = config.get("port", "2049")
    version = config.get("nfs_version", "4.2")
    fs_name = "cephfs"
    nfs_name = "cephfs-nfs"
    nfs_export = "/export"
    nfs_mount = "/mnt/nfs"
    fs = "cephfs"
    operation = config.get("operation", None)
    cluster_bw = config["cluster_bw"][0]
    export_bw = config["export_bw"][0]
    if not nfs_nodes:
        raise OperationFailedError("No NFS nodes found in cluster")

    nfs_node = nfs_nodes[0]
    qos_type = config.get("qos_type", None)
    subvolume_group = "ganeshagroup"

    no_clients = int(config.get("clients", "1"))
    if no_clients > len(clients):
        raise ConfigError("The test requires more clients than available")

    clients = clients[:no_clients]
    client = clients[0]
    ceph_nfs_client = Ceph(client).nfs
    Ceph(client).fs.sub_volume_group.create(volume=fs_name, group=subvolume_group)

    try:
        # Setup nfs cluster
        setup_nfs_cluster(
            clients,
            nfs_node.hostname,
            port,
            version,
            nfs_name,
            nfs_mount,
            fs_name,
            nfs_export,
            fs,
            ceph_cluster=ceph_cluster,
        )

        # Process QoS operations
        nfs_export = "{0}_0".format(nfs_export)
        # Enable QoS with parameters
        enable_disable_qos_for_cluster(
            enable_flag=True,
            ceph_cluster_nfs_obj=ceph_nfs_client.cluster,
            cluster_name=cluster_name,
            qos_type=qos_type,
            **{
                k: cluster_bw[k]
                for k in [
                    "max_export_write_bw",
                    "max_export_read_bw",
                    "max_client_write_bw",
                    "max_client_read_bw",
                ]
                if k in cluster_bw
            },
        )

        # enable QoS for export
        enable_disable_qos_for_export(
            enable_flag=True,
            ceph_export_nfs_obj=ceph_nfs_client.export,
            cluster_name=cluster_name,
            qos_type=qos_type,
            nfs_name=nfs_name,
            export=nfs_export,
            **{
                k: export_bw[k]
                for k in [
                    "max_export_write_bw",
                    "max_export_read_bw",
                    "max_client_write_bw",
                    "max_client_read_bw",
                ]
                if k in export_bw
            },
        )

        if operation == "restart":
            # Get nfs service name
            data = json.loads(Ceph(client).orch.ls(format="json"))
            [service_name] = [
                x["service_name"] for x in data if x.get("service_id") == cluster_name
            ]

            # restart the service
            Ceph(client).orch.restart(service_name)
            if cluster_name not in [x["service_name"] for x in data]:
                sleep(5)

            export_data = ceph_nfs_client.export.get(
                nfs_name=nfs_name, nfs_export=nfs_export
            )
            # validate if QOS data persists after cluster restart
            export_data_after_restart = ceph_nfs_client.export.get(
                nfs_name=nfs_name, nfs_export=nfs_export
            )
            if not export_data:
                raise OperationFailedError("Failed to create nfs export")

            export_data_after_restart = json.loads(export_data_after_restart)
            log.info("export_data_after_restart: {0}".format(export_data_after_restart))

            if float(
                re.findall(
                    r"\d+.\d+",
                    export_data_after_restart["qos_block"]["max_export_write_bw"],
                )[0]
            ) == float(re.findall(r"\d+", export_bw["max_export_write_bw"])[0]):
                log.info(
                    "Qos data for {0} for export persists even after the nfs cluster restarted".format(
                        qos_type
                    )
                )
            else:
                raise OperationFailedError(
                    "Qos data for {0} did not persists after the nfs cluster restarted".format(
                        qos_type
                    )
                )

        verify_bw_speed(client, nfs_mount, qos_type, export_bw)

        if operation == "dynamic_update":
            # Update the qos parameters
            export_bw = {
                "max_client_read_bw": "6MB",
                "max_client_write_bw": "6MB",
                "max_export_read_bw": "6MB",
                "max_export_write_bw": "6MB",
            }

            enable_disable_qos_for_export(
                enable_flag=True,
                ceph_export_nfs_obj=ceph_nfs_client.export,
                cluster_name=cluster_name,
                qos_type=qos_type,
                nfs_name=nfs_name,
                export=nfs_export,
                **{
                    k: export_bw[k]
                    for k in [
                        "max_export_write_bw",
                        "max_export_read_bw",
                        "max_client_write_bw",
                        "max_client_read_bw",
                    ]
                    if k in export_bw
                },
            )

        verify_bw_speed(client, nfs_mount, qos_type, export_bw)

        # Disable QoS for export
        enable_disable_qos_for_export(
            enable_flag=False,
            nfs_name=nfs_name,
            export=nfs_export,
            ceph_export_nfs_obj=ceph_nfs_client.export,
            cluster_name=cluster_name,
        )

        enable_disable_qos_for_cluster(
            enable_flag=False,
            qos_type=qos_type,
            ceph_cluster_nfs_obj=ceph_nfs_client.cluster,
            cluster_name=cluster_name,
        )
        return 0
    except (ConfigError, OperationFailedError, RuntimeError) as e:
        log.error("Test failed: {0}".format(e))
        return 1
    finally:
        log.info("Cleanup in progress")
        log.debug("deleting NFS cluster {0}".format(cluster_name))
        cleanup_cluster(clients, nfs_mount, nfs_name, nfs_export)
