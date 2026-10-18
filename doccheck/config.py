"""配置读写：config.yaml 是合同基准、时间线、黑白名单的持久化存储。"""
import os
import yaml

DEFAULT_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config.yaml")


def load_config(path: str = DEFAULT_PATH) -> dict:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def save_config(cfg: dict, path: str = DEFAULT_PATH):
    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump(cfg, f, allow_unicode=True, sort_keys=False)


def confirm_contract(profile, path: str = DEFAULT_PATH):
    """页面1 用户确认合同信息后调用：写入基准并置 confirmed=true。"""
    cfg = load_config(path)
    c = cfg.setdefault("contract", {})
    c.update({
        "confirmed": True,
        "project_name": profile.project_name,
        "employer": profile.employer,
        "contractors": profile.contractors,
        "supervisor": profile.supervisor,
        "amount_number": profile.amount_number,
        "amount_cn": profile.amount_cn,
        "payment_terms": profile.payment_terms,
        "service_period": profile.service_period,
        "contract_no": profile.contract_no,
    })
    save_config(cfg, path)
