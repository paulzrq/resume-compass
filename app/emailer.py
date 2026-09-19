"""
简历罗盘 · 报告自动存档邮件
2026-09-19：免费版 Streamlit Cloud 的容器是"用完即焚"的——reports/ 文件夹存的PDF只活在当次
容器的磁盘上，代码一有更新触发重新部署、或者12小时没人访问被自动唤醒重启，之前生成的报告
就全没了，没有任何长期留存机制。这个模块用最省事的办法补上这个缺口：每次生成完PDF，
顺手用SMTP把报告当附件发一封邮件出去，邮箱本身就是最简单的"数据库"——收件箱能查、能搜、
不会因为容器重启就消失。

发件账号是Paul自己的企业邮箱 paul.zhang@graceharborus.com（Microsoft 365 / Outlook托管），
所以SMTP服务器写死成 smtp.office365.com。如果以后企业邮箱换了服务商，改SMTP_HOST/SMTP_PORT
这两个常量就行，不用改调用方（app.py）的代码。
"""
import smtplib
import ssl
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Optional

import streamlit as st

SMTP_HOST = "smtp.office365.com"
SMTP_PORT = 587  # Microsoft 365 走 STARTTLS，用587端口（不是SSL专用的465）


def _get_email_config() -> Optional[dict]:
    """从 st.secrets 读发件账号配置。SMTP_SENDER_EMAIL / SMTP_SENDER_PASSWORD 只要有一个没配置，
    就当作"这个功能还没开启"处理，返回None——不报错、不阻断主流程，本地开发机没配置这些
    完全不影响正常用app（跟 _get_secret_api_key() 的兜底思路一致）。"""
    try:
        sender = st.secrets.get("SMTP_SENDER_EMAIL", "")
        password = st.secrets.get("SMTP_SENDER_PASSWORD", "")
        recipient = st.secrets.get("REPORT_RECIPIENT_EMAIL", "") or sender
    except Exception:
        return None
    if not sender or not password:
        return None
    return {"sender": sender, "password": password, "recipient": recipient}


def send_report_email(
    pdf_bytes: bytes,
    filename: str,
    student_name: str,
    field_name: str,
    total_score,
    tier_label: str,
    timestamp: str,
) -> None:
    """把评估报告当附件发邮件存档，供以后翻查。
    Secrets没配置齐的话直接静默跳过（返回，不抛异常）——这种情况下调用方看不出区别，
    因为本来就没开启这功能。配置齐了但发送过程本身出错（密码错、网络问题、企业邮箱那边
    没开SMTP AUTH等）会抛异常，由调用方（app.py）负责兜底捕获并提示，绝不能让这一步的
    失败影响用户已经看到的评分结果和PDF下载。"""
    config = _get_email_config()
    if config is None:
        return

    msg = MIMEMultipart()
    msg["Subject"] = f"【简历罗盘存档】{student_name} · {field_name} · {total_score}分"
    msg["From"] = config["sender"]
    msg["To"] = config["recipient"]

    body = (
        f"学生姓名：{student_name}\n"
        f"目标方向：{field_name}\n"
        f"综合得分：{total_score}/100（{tier_label}）\n"
        f"评估时间：{timestamp}\n\n"
        f"完整评估报告见附件PDF。此邮件由简历罗盘自动发送，用于存档。"
    )
    msg.attach(MIMEText(body, "plain", "utf-8"))

    attachment = MIMEApplication(pdf_bytes, _subtype="pdf")
    attachment.add_header("Content-Disposition", "attachment", filename=filename)
    msg.attach(attachment)

    context = ssl.create_default_context()
    with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=20) as server:
        server.starttls(context=context)
        server.login(config["sender"], config["password"])
        server.sendmail(config["sender"], [config["recipient"]], msg.as_string())
