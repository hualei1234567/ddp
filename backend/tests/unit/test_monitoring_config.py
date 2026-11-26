"""监控配置结构校验（阶段 4 / 审计 R2）。

目的：告警规则与抓取配置是**运维资产**，一样会因为改名/手误而失效。
本测试把三件事钉死，不依赖真实 Prometheus 进程：

1. 三份 YAML 可解析且结构完整（必需字段、必填 annotations）
2. 告警 expr 里引用的 `ddp_*` 指标名**必须真实存在于** `app/core/metrics.py`
   （防止指标改名后告警静默失效——这是最隐蔽的运维故障）
3. compose 里的监控服务挂了正确的配置与 profile
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[3]
MONITORING = REPO / "infra" / "monitoring"
METRICS_PY = REPO / "backend" / "app" / "core" / "metrics.py"

# 允许出现的非 ddp_ 前缀指标（Prometheus 内建）
_BUILTIN_METRICS = {"up", "time"}


def _load(name: str) -> dict:
    return yaml.safe_load((MONITORING / name).read_text(encoding="utf-8"))


def test_prometheus_configs_scrape_api_metrics() -> None:
    """本地与生产两份抓取配置都必须指向 api 的 /metrics。"""
    for name in ("prometheus.yml", "prometheus.prod.yml"):
        config = _load(name)
        jobs = {job["job_name"]: job for job in config["scrape_configs"]}
        assert "ddp-api" in jobs, f"{name} 缺少 ddp-api 抓取任务"
        job = jobs["ddp-api"]
        assert job["metrics_path"] == "/metrics"
        assert job["static_configs"][0]["targets"] == ["api:8000"]
        # 规则文件必须挂载，否则告警不会加载
        assert "/etc/prometheus/alerts.yml" in config["rule_files"]
        assert config["alerting"]["alertmanagers"][0]["static_configs"][0]["targets"] == [
            "alertmanager:9093"
        ]


def test_prod_config_requires_token_local_does_not() -> None:
    """生产必须带 Bearer 令牌；本地（DDP_ENV=local 放行）不带。"""
    prod = _load("prometheus.prod.yml")["scrape_configs"][0]
    assert prod["authorization"]["type"] == "Bearer"
    assert prod["authorization"]["credentials_file"] == "/etc/prometheus/metrics_token"

    local = _load("prometheus.yml")["scrape_configs"][0]
    assert "authorization" not in local


def test_alert_rules_have_required_fields() -> None:
    """每条规则必须有 for / severity / summary / description，且名称唯一。"""
    config = _load("alerts.yml")
    names: list[str] = []
    for group in config["groups"]:
        assert group["rules"], f"告警组 {group['name']} 为空"
        for rule in group["rules"]:
            assert rule["alert"], "规则缺少 alert 名称"
            names.append(rule["alert"])
            assert "expr" in rule and rule["expr"].strip()
            # for 允许 "0m"（立即触发），但必须显式声明
            assert "for" in rule, f"{rule['alert']} 缺少 for（会因抖动误报）"
            labels = rule.get("labels", {})
            assert labels.get("severity") in {"critical", "warning", "info"}
            annotations = rule.get("annotations", {})
            for field in ("summary", "description"):
                assert annotations.get(field), f"{rule['alert']} 缺少 annotations.{field}"
    assert len(names) == len(set(names)), f"告警名称重复：{names}"


def test_alert_metric_names_exist_in_code() -> None:
    """告警 expr 引用的 ddp_* 指标必须真实存在于 metrics.py。

    这是本文件最重要的断言：指标改名而告警没跟着改，Prometheus 不会报错，
    规则会永远不触发——属于「沉默的失效」。
    """
    metrics_source = METRICS_PY.read_text(encoding="utf-8")
    declared = set(re.findall(r'"(ddp_[a-z_0-9]+)"', metrics_source))
    assert declared, "未能从 metrics.py 提取任何指标名（正则或文件结构已变）"

    config = _load("alerts.yml")
    referenced: set[str] = set()
    for group in config["groups"]:
        for rule in group["rules"]:
            referenced |= set(re.findall(r"\bddp_[a-z_0-9]+", rule["expr"]))

    unknown = referenced - declared
    assert not unknown, f"告警引用了不存在的指标：{sorted(unknown)}（已声明：{sorted(declared)}）"


def test_alertmanager_route_and_receiver() -> None:
    """Alertmanager 必须有默认接收端与分组策略。"""
    config = _load("alertmanager.yml")
    assert config["route"]["receiver"]
    assert config["route"]["group_by"]
    assert config["route"]["repeat_interval"]
    receivers = {r["name"] for r in config["receivers"]}
    assert config["route"]["receiver"] in receivers
    for name in config["route"].get("routes", []):
        assert name["receiver"] in receivers, f"路由接收端 {name['receiver']} 未定义"


def test_compose_monitoring_services_wired() -> None:
    """compose 里监控服务必须挂配置、用 monitoring profile、暴露端口。"""
    compose = yaml.safe_load((REPO / "docker-compose.yml").read_text(encoding="utf-8"))
    services = compose["services"]
    for name in ("prometheus", "alertmanager"):
        assert name in services, f"compose 缺少 {name} 服务"
        assert services[name]["profiles"] == ["monitoring"]
        assert services[name]["volumes"], f"{name} 未挂载配置"
        assert services[name]["ports"], f"{name} 未暴露管理端口"

    prom_volumes = " ".join(services["prometheus"]["volumes"])
    assert "prometheus.yml" in prom_volumes
    assert "alerts.yml" in prom_volumes
    assert "alertmanager.yml" in " ".join(services["alertmanager"]["volumes"])

    # 命名卷必须声明，否则 compose 启动即报错
    assert "ddp-promdata" in compose["volumes"]
    assert "ddp-amdata" in compose["volumes"]
