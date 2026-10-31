# -*- coding: utf-8 -*-
"""mailer.py — 邮件试验通道（T05 §6.2）

把当日事中日报（+ 摘要正文）发到业务邮箱。设计约束：
- **失败不抛异常**：邮件是试验性质，绝不能因为 SMTP 挂了把整条管线拖死，
  所有失败都以 (False, 原因) 返回并写进管线日志。
- **一天最多一封**：state/mail_state.json 记 lastMailDate，按品类去重。
- 凭据全部来自规则键（mail_host/mail_port/mail_user/mail_auth_code/mail_to），
  不写死、不落明文到代码里。
"""

import json
import smtplib
from datetime import datetime
from email.header import Header
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

from app.config import STATE_DIR

_MAIL_STATE = STATE_DIR / "mail_state.json"


def _load_state():
    try:
        return json.loads(_MAIL_STATE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_state(state):
    try:
        _MAIL_STATE.write_text(json.dumps(state, ensure_ascii=False, indent=1),
                               encoding="utf-8")
    except OSError:
        pass


def _summary_lines():
    """正文摘要：当日新超期 + 持续超期 + 预警（Top 若干行）。"""
    from app.inflight_service import compute_alerts_today
    alerts = compute_alerts_today()
    lines = []
    for title, rows, cap in (
            ("当日新超期", alerts.get("todayNewOverdue") or [], 10),
            ("持续超期", alerts.get("ongoingOverdue") or [], 10),
            ("临近超期预警(85%)", alerts.get("warnings") or [], 5)):
        lines.append(f"【{title}】共 {len(rows)} 条")
        for r in rows[:cap]:
            lines.append(f"  · {r.get('sku')} {r.get('dept')} {r.get('seg')} "
                         f"超{r.get('overDays', r.get('elapsed', '—'))}"
                         f"{' 天' if r.get('overDays') is not None else ''}")
        if len(rows) > cap:
            lines.append(f"  ……其余 {len(rows) - cap} 条见附件日报")
        lines.append("")
    return "\n".join(lines) or "今日无超期/预警。"


def send_daily_report(category, report_path, date_str):
    """发送事中日报邮件。返回 (ok, reason)。失败只记原因，绝不抛出。"""
    try:
        from app.rule_service import get_rules
        rules, _ = get_rules()

        if not rules.get("mail_enabled"):
            return False, "mail_enabled=false（邮件通道未开启）"
        host = (rules.get("mail_host") or "").strip()
        port = int(rules.get("mail_port") or 465)
        user = (rules.get("mail_user") or "").strip()
        auth = (rules.get("mail_auth_code") or "").strip()
        to = [x.strip() for x in (rules.get("mail_to") or "").split(",")
              if x.strip()]
        if not (host and user and auth and to):
            return False, "邮件配置不完整（host/user/auth_code/to）"

        # 一天一封：按品类去重
        state = _load_state()
        if state.get(category) == date_str:
            return False, f"{date_str} 已发过（去重跳过）"

        if not report_path or not Path(report_path).exists():
            return False, f"日报文件不存在：{report_path}"

        msg = MIMEMultipart()
        msg["From"] = user
        msg["To"] = ", ".join(to)
        msg["Subject"] = Header(
            f"新品流程事中日报 {date_str}（{category}）", "utf-8")
        body = MIMEText(
            f"新品流程事中日报 {date_str}（{category}）\n\n"
            f"{_summary_lines()}\n"
            f"—— 数据后台系统自动管线\n", "plain", "utf-8")
        msg.attach(body)
        with open(report_path, "rb") as f:
            att = MIMEApplication(f.read())
        att.add_header("Content-Disposition", "attachment",
                       filename=Path(report_path).name.encode("utf-8")
                       .decode("latin-1"))
        msg.attach(att)

        with smtplib.SMTP_SSL(host, port, timeout=30) as s:
            s.login(user, auth)
            s.sendmail(user, to, msg.as_string())

        state[category] = date_str
        state[f"{category}:at"] = datetime.now().strftime("%Y-%m-%d %H:%M")
        _save_state(state)
        return True, f"已发送至 {len(to)} 个收件人"
    except Exception as e:                      # noqa: BLE001 —— 邮件失败不拖垮管线
        return False, f"{type(e).__name__}: {e}"
