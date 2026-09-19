"""
简历罗盘 · 报告自动存档邮件
2026-09-19：免费版 Streamlit Cloud 的容器是"用完即焚"的——reports/ 文件夹存的PDF只活在当次
容器的磁盘上，代码一有更新触发重新部署、或者12小时没人访问被自动唤醒重启，之前生成的报告
就全没了，没有任何长期留存机制。这个模块用最省事的办法补上这个缺口：每次生成完PDF，
顺手用SMTP把报告和原版简历分别当附件发一封邮件出去，邮箱本身就是最简单的"数据库"——
收件箱能查、能搜、不会因为容器重启就消失。

2026-09-19 补充：企业邮箱 paul.zhang@graceharborus.com 那边卡在Microsoft 365的租户级
SMTP AUTH权限+应用密码开关上，来回找IT配置比较费时间，改用Paul自己的Gmail账号
resume.compass.v1@gmail.com 当发件方，SMTP服务器相应换成 smtp.gmail.com。收件人不变，
还是发到 paul.zhang@graceharborus.com。Gmail发信同样需要"应用专用密码"（不是登录密码，
Gmail几年前就彻底关闭了账号密码直登SMTP这条路），生成方式：先在Google账号安全设置里开
"两步验证"，再去 myaccount.google.com/apppasswords 生成一个16位专用密码，填进secrets
的 SMTP_SENDER_PASSWORD 里。如果以后发件邮箱又换了服务商，改SMTP_HOST/SMTP_PORT这两个
常量就行，不用改调用方（app.py）的代码。

2026-09-19 补充：原本只附评估报告PDF一个文件，现在改成报告PDF + 学生上传的原版简历
（可能是PDF，也可能是PNG/JPG/WEBP图片）两个附件分开发——所以附件构造这块换成了通用的
_build_attachment()，靠文件名后缀猜MIME类型，PDF、图片统一处理，不用为每种格式单独写。
"""
import mimetypes
import smtplib
import ssl
from email.mime.base import MIMEBase
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email import encoders
from typing import Optional

import streamlit as st

SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 587  # Gmail 也走 STARTTLS，用587端口（不是SSL专用的465）


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


def _build_attachment(data: bytes, filename: str) -> MIMEBase:
    """通用附件构造：靠文件名后缀猜MIME类型（PDF、PNG、JPG、WEBP都能猜对），猜不出来的
    统一按 application/octet-stream 处理——不管是评估报告PDF还是学生上传的原版简历
    （PDF或图片），都走这一个函数，不用为每种格式单独写一遍attach逻辑。"""
    mime_type, _ = mimetypes.guess_type(filename)
    if mime_type:
        maintype, subtype = mime_type.split("/", 1)
    else:
        maintype, subtype = "application", "octet-stream"
    part = MIMEBase(maintype, subtype)
    part.set_payload(data)
    encoders.encode_base64(part)
    part.add_header("Content-Disposition", "attachment", filename=filename)
    return part


def send_report_email(
    pdf_bytes: bytes,
    filename: str,
    student_name: str,
    field_name: str,
    total_score,
    tier_label: str,
    timestamp: str,
    resume_bytes: Optional[bytes] = None,
    resume_filename: Optional[str] = None,
) -> None:
    """把评估报告PDF、以及学生上传的原版简历（如果有），分别当附件发邮件存档，供以后翻查。
    resume_bytes/resume_filename 传None就只发报告这一个附件（不报错，正常发送）——
    调用方app.py理论上每次评估都能拿到原版简历，但这里做成可选参数是为了防御性兜底：
    万一以后哪个上传分支忘了存原文件字节，也不会导致整个邮件发送失败。

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

    has_resume_attachment = bool(resume_bytes and resume_filename)
    body = (
        f"学生姓名：{student_name}\n"
        f"目标方向：{field_name}\n"
        f"综合得分：{total_score}/100（{tier_label}）\n"
        f"评估时间：{timestamp}\n\n"
        + (
            "附件包含：评估报告PDF + 学生上传的原版简历。"
            if has_resume_attachment
            else "附件包含：评估报告PDF（没有拿到原版简历文件，只有报告）。"
        )
        + "\n此邮件由简历罗盘自动发送，用于存档。"
    )
    msg.attach(MIMEText(body, "plain", "utf-8"))

    msg.attach(_build_attachment(pdf_bytes, filename))
    if has_resume_attachment:
        msg.attach(_build_attachment(resume_bytes, resume_filename))

    context = ssl.create_default_context()
    with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=20) as server:
        server.starttls(context=context)
        server.login(config["sender"], config["password"])
        server.sendmail(config["sender"], [config["recipient"]], msg.as_string())
