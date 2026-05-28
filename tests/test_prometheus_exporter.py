import os
import unittest
from unittest import mock

from prodesk.prometheus_exporter import PrometheusExporter


class PrometheusExporterTests(unittest.TestCase):
    def test_start_binds_all_interfaces_in_docker_mode_when_metrics_host_is_localhost(self):
        exporter = PrometheusExporter(
            {
                "enabled": True,
                "host": "127.0.0.1",
                "port": 9108,
                "namespace": "prodesk",
            }
        )
        server = mock.Mock()
        thread = mock.Mock()
        with mock.patch.dict(os.environ, {"BRO_DOCKER_MODE": "1"}, clear=False), mock.patch(
            "prodesk.prometheus_exporter.CollectorRegistry",
            return_value=object(),
        ), mock.patch("prodesk.prometheus_exporter.Gauge"), mock.patch(
            "prodesk.prometheus_exporter.start_http_server",
            return_value=(server, thread),
        ) as start_http_server:
            exporter.start()

        self.assertEqual(start_http_server.call_args.kwargs["addr"], "0.0.0.0")
        exporter.stop()
        server.shutdown.assert_called_once()

    def test_start_preserves_localhost_bind_outside_docker_mode(self):
        exporter = PrometheusExporter(
            {
                "enabled": True,
                "host": "127.0.0.1",
                "port": 9108,
                "namespace": "prodesk",
            }
        )
        with mock.patch.dict(os.environ, {"BRO_DOCKER_MODE": "0"}, clear=False), mock.patch(
            "prodesk.prometheus_exporter.CollectorRegistry",
            return_value=object(),
        ), mock.patch("prodesk.prometheus_exporter.Gauge"), mock.patch(
            "prodesk.prometheus_exporter.start_http_server",
            return_value=(mock.Mock(), mock.Mock()),
        ) as start_http_server:
            exporter.start()

        self.assertEqual(start_http_server.call_args.kwargs["addr"], "127.0.0.1")


if __name__ == "__main__":
    unittest.main()
