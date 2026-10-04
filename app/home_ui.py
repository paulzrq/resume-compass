"""Presentation-only English home screen; scoring keeps canonical field IDs."""
import base64
import functools
from html import escape
from pathlib import Path

import streamlit as st
from streamlit.errors import StreamlitAPIException

from mascots import mascot_path, mascot_accent_color

FIELD_LABELS = {'swe': 'Software Engineer', 'ds': 'Data Scientist', 'mle': 'Machine Learning Engineer', 'engineering': 'Hardware Engineer', 'cybersecurity': 'Cybersecurity Engineer', 'robotics': 'Robotics / Automation Engineer', 'fintech_eng': 'Fintech Engineer', 'finance': 'Investment Banking Analyst', 'risk_analyst': 'Risk Analyst', 'actuary': 'Actuary', 'consulting': 'Consultant', 'marketing': 'Marketing', 'pm': 'Product Manager', 'ba': 'Business Analyst', 'ops': 'Supply Chain Management', 'data_analyst': 'Data Analyst', 'accounting': 'Accountant / Auditor', 'hr': 'Human Resources', 'sales': 'Sales', 'law': 'Lawyer', 'clinical_research': 'Clinical Research Assistant', 'media': 'Media / Public Relations', 'film_production': 'Film Producer', 'teacher': 'Teacher', 'ux': 'UI/UX Designer', 'graphic_design': 'Graphic Designer', 'architecture': 'Architect', 'game_design': 'Game Designer'}
# framework.json categories are in English now (i18n); keep the old Chinese keys
# as fallback so both languages resolve.
CATEGORY_LABELS = {"技术与工程":"Technology & Engineering","商业与管理":"Business & Management","专业服务与内容":"Professional Services & Content","设计与创意":"Design & Creative",
                   "Technology & Engineering":"Technology & Engineering","Business & Management":"Business & Management","Professional Services & Content":"Professional Services & Content","Design & Creative":"Design & Creative"}

# 2026-10-02：职业卡（选定后）用的是去白底的插画，专门为这张卡片准备，跟 report.py /
# share_card.py / 加载动画等其它所有引用 mascot_path() 的地方（它们都假定插画是不透明白底，
# 直接用，不想动）完全分开，互不影响。这批透明版图片是离线用 OpenCV 泛洪填充批量抠出来的
# （app/scripts/make_transparent_mascots.py，28 张跑一次，结果存成文件，不在请求里现算——所以
# requirements.txt 不需要加 opencv/numpy 这种本来没有的依赖）。哪天要换插画、加新方向，记得
# 把新图也跑一遍那个脚本，再把结果放进 mascots_transparent/。
MASCOTS_TRANSPARENT_DIR = Path(__file__).resolve().parent / "mascots_transparent"


@functools.lru_cache(maxsize=None)
def _mascot_transparent_b64(field_id: str):
    """选定状态职业卡用的去白底插画（base64）；没有插画或抠图版缺失时返回 None，
    调用方要能优雅降级（不显示插画，只显示文字）。"""
    path = mascot_path(field_id)
    if not path:
        return None
    transparent_path = MASCOTS_TRANSPARENT_DIR / path.name
    if not transparent_path.is_file():
        return None
    return base64.b64encode(transparent_path.read_bytes()).decode("ascii")


# 2026-10-03：踩过一个坑——一开始以为 Streamlit 会给页面暴露 --background-color /
# --text-color / --secondary-background-color 这些 CSS 变量，实际翻了一遍 Streamlit 1.50
# 打包出来的前端代码，根本没有这几个变量（翻遍 static/js 和 static/css 都搜不到），
# var(--background-color,#fff) 这种写法里的变量永远是"没定义"，等于永远在用后面那个
# 白色兜底值——所以深色模式下卡片一直是刺眼的白块，跟 Paul 反馈的一样，是真 bug 不是没改到位。
# 正确做法是用 st.context.theme.type（Streamlit 自己读取/推断出的当前主题，"dark"/"light"）
# 在 Python 这边直接判断，把对应的颜色字面量拼进 CSS 里，而不是指望浏览器端的 CSS 变量。
def _theme_tokens():
    """返回当前主题（深色/浅色）对应的一组颜色字面量，直接拼进自定义 CSS 里用。"""
    try:
        dark = st.context.theme.type == "dark"
    except Exception:
        dark = False
    if dark:
        return dict(
            bg="#1c1c1e", bg2="#2a2a2d", text="#f5f5f7", sub_text="#f5f5f7",
            border="#ffffff1f", dropzone_bg="#ffffff0a", hover_bg="#333336",
        )
    return dict(
        bg="#ffffff", bg2="#f5f5f7", text="#1d1d1f", sub_text="#1d1d1f",
        border="#88888822", dropzone_bg="#8b9aaa0d", hover_bg="#ffffff",
    )


# 2026-10-03：加了个全局 max-width（居中），不然 app.py 里 layout="wide" 会让整个首页一路铺到
# 浏览器边缘——跟设计稿里居中、留白的版式差很远。
# 2026-10-03 又调小了一版：max-width 1120 -> 1000，宽屏下卡片本身更窄一点；另外 layout="wide"
# 这个页面级设置本身会让最外层容器两侧内边距缩得很小（接近 0），只给卡片本身设 max-width 还不够
# ——窗口宽度刚好接近 1000px 时卡片会直接贴到浏览器边缘。所以额外给最外层容器
# （stMainBlockContainer）也加了左右内边距，用 :has(.st-key-home_workspace) 限定只在首页这个
# 容器生效，不会影响到应用里其它用了 wide 布局的页面。
def _build_css(t):
    css = '\n[data-testid="stMainBlockContainer"]:has(.st-key-home_workspace){padding-left:3rem!important;padding-right:3rem!important}'
    css += f'\n.home-hero{{text-align:center;padding:38px 0 34px;color:{t["text"]};max-width:1000px;margin:0 auto}}.home-hero h2{{font-size:clamp(30px,4.2vw,52px)!important;letter-spacing:-1.8px;font-weight:700;padding:0!important;line-height:1.12}}.home-hero h2 .accent{{background:linear-gradient(90deg,#0071e3,#8b5cf6);-webkit-background-clip:text;background-clip:text;color:transparent}}.home-hero p{{font-size:18px;opacity:.62;margin-top:16px}}'
    css += f'\n.st-key-home_workspace{{max-width:1000px;margin:0 auto;padding:32px;border-radius:24px;background:{t["bg"]};box-shadow:0 8px 40px #15233708;border:1px solid {t["border"]}}}'
    # 2026-10-04 十三版：Paul 反馈"简历上传之后不要在下面单独开一行，要放在框里"——翻了
    # Streamlit 前端打包代码（stFileUploader 相关 testid）确认了真实 DOM 结构：外层
    # [data-testid="stFileUploader"] 下面是 label（隐藏）+ Dropzone（空状态提示）+ 文件列表
    # （上传完才渲染，是 Dropzone 的"下一个兄弟节点"，不是 Dropzone 内部的子节点）——之前固定
    # 高度/边框/圆角/背景全部加在 stFileUploaderDropzone 自己身上，所以一旦文件列表作为兄弟节点
    # 出现，必然是 Dropzone 这个框下面另起一行，不可能"放进框里"，这是布局结构决定的，不是哪个
    # 属性漏加 !important。真正的修法是把"这是一个固定高度的框"这件事从 Dropzone 身上挪到外层
    # stFileUploader 上——边框/圆角/背景/固定高度都搬到 stFileUploader，Dropzone 自己改成
    # 透明无边框、flex:1 占满剩余空间（没文件时就是整个框），文件列表则用 :has() 检测到
    # [data-testid="stFileUploaderFile"] 存在时把 Dropzone 隐藏、同时让 stFileUploader 用
    # justify-content:center 把文件行纵向居中——这样不管有没有文件，视觉上始终是同一个边框完整的
    # 盒子，只是里面的内容切换。（注：真实文件上传没法在自动化里稳定模拟出来——试过用
    # DataTransfer+File 构造合成事件，Streamlit 前端的 React 状态不认——所以这版空状态在浏览器
    # 里验证过，"文件已上传"的样子需要 Paul 自己拿真实简历试一下确认。）
    css += f'\n.st-key-home_workspace [data-testid="stFileUploader"]{{height:320px!important;min-height:320px!important;max-height:320px!important;box-sizing:border-box!important;border:1px dashed #9aa9ba66;border-radius:16px;background:{t["dropzone_bg"]};overflow:hidden;display:flex!important;flex-direction:column!important}}'
    css += '\n.st-key-home_workspace [data-testid="stFileUploader"]:has([data-testid="stFileUploaderFile"]){justify-content:center!important}'
    css += '\n.st-key-home_workspace [data-testid="stFileUploaderDropzone"]{flex:1 1 auto!important;height:auto!important;min-height:0!important;max-height:none!important;box-sizing:border-box!important;border:none!important;border-radius:0!important;background:transparent!important;display:flex;flex-direction:column;justify-content:center;gap:18px;align-items:center;overflow:hidden}'
    css += '\n.st-key-home_workspace [data-testid="stFileUploader"]:has([data-testid="stFileUploaderFile"]) [data-testid="stFileUploaderDropzone"]{display:none!important}'
    css += '\n.st-key-home_workspace [data-testid="stFileUploaderFile"]{width:100%;max-width:88%;margin:0 auto;box-sizing:border-box;padding:16px 20px;border-radius:14px;background:{BG};box-shadow:0 2px 14px #15233714}'.replace('{BG}', t["bg"])
    css += '\n.st-key-home_workspace [data-testid="stFileUploaderDropzoneInstructions"]{display:flex!important;align-items:center;text-align:center}'
    css += '\n.st-key-home_workspace [data-testid="stFileUploaderDropzone"] button{color:#0071e3;border-radius:30px;border:1px solid #0071e355}'
    css += '\n.st-key-home_workspace [data-testid="stBaseButton-primary"]{background:#0071e3;border:0;border-radius:28px;min-height:48px;padding:10px 32px;font-weight:600;color:white}'
    css += '\n.st-key-home_workspace [data-testid="stBaseButton-primary"]:disabled{opacity:.4}'
    css += f'\n.home-section{{font-size:21px;font-weight:650;margin:0 0 18px;color:{t["text"]}}}.home-foot{{text-align:center;font-size:12px;opacity:.55;padding-top:20px}}'
    css += '\n@media(max-width:640px){[data-testid="stMainBlockContainer"]:has(.st-key-home_workspace){padding-left:1rem!important;padding-right:1rem!important}.home-hero{padding:24px 0}.home-hero h2{letter-spacing:-1px}.home-hero p{font-size:15px}.st-key-home_workspace{padding:18px 14px;border-radius:20px}.st-key-home_workspace [data-testid="stFileUploader"]{height:260px!important;min-height:260px!important;max-height:260px!important}.home-section{font-size:18px}.st-key-home_workspace [data-testid="stBaseButton-primary"]{width:100%}}'

    css += '\n.st-key-home_workspace [data-testid="stFileUploaderDropzone"]::before{content:"Drop your resume here";font-size:18px;font-weight:600;display:block;margin-top:14px}.st-key-home_workspace [data-testid="stFileUploaderDropzone"]>div{align-items:center;justify-content:center}'

    # 2026-10-02：职业路径选择器——从 sac.cascader 换成 st.popover + st.pills。
    # 2026-10-03：改了四版——
    #   1) 没选之前不再需要先点开一个大按钮才能看到分类/角色，打开就能直接选。
    #   2) 整个选择器包进 st.fragment：分类/角色的交互只在 fragment 内部重跑，不会带着整个页面
    #      一起变灰/闪一下。只有真正选定一个角色/自定义方向时才跳出 fragment 做一次全页重跑。
    #   3) 改成"一级分类 -> 二级角色 -> 选定结果"的三步导航，卡片左上角一个返回箭头。每一步
    #      只显示一层内容，卡片高度能固定成和上传框一样（CARD_H，260px / 移动端 220px）。
    #   4) 四版：分类/角色不再用竖排的一行一个的 list（Paul 反馈"不要作为一个 list 下拉"），
    #      改成跟当初 pills 版一样的"胶囊卡片"外观——一行能放下就并排，放不下自动换行
    #      （flex-wrap），当前选中的那个（返回到某一步时）用蓝色高亮，视觉上更像选择卡片而不是
    #      下拉菜单。同时补上 .st-key-home_career_card 的 overflow:hidden——四版之前这里漏加了，
    #      内容一旦比 260px 高就会整个溢出卡片、把布局撑得很乱（Paul 截图里那种巨大空白间距的
    #      根源之一）。
    # 2026-10-04 十一版：Paul 这次反问确认了三件事——(1) 选定角色这一步用 scope="app" 带来的
    # 全页变暗还是太明显，改成跟分类/角色一样 fragment 内部重跑（见 _advance 调用处注释，代价是
    # Start assessment 按钮会滞后一拍解锁）；(2) 插画要基本打满卡片高度，而不是局部放大——这版
    # 从 260px 提到 280px（卡片净高约 292px，留一点边距），移动端从 170px 提到 210px；
    # (3) 角色列表"显得拥挤"的真正原因是 .st-key-home_career_role_scroll 的 flex:1;min-height:0
    # 没加 !important，跟之前 .st-key-home_career_card 踩的是同一个 flexbox 坑（Streamlit 自己
    # 的 flex-basis:0% 默认值把它挤没了），内容一多就不走内部滚动、直接溢出到外层卡片，被外层
    # overflow:hidden 硬裁掉半个胶囊（Paul 截图里 Fintech Engineer 那个）。这版补上 !important，
    # 顺带把胶囊的内边距/字号/间距都调大了一圈，标题下边距也加了，视觉上没那么挤。
    # 补充：只加 !important 还不够——在推给 Paul 之前，用浏览器开了个全新的 tab（避开之前调试
    # session 里残留的注入样式干扰）实测了一遍角色列表页，发现角色列表直接空白了，一个胶囊都不
    # 显示！查下来是 Streamlit 新版把每个 st.container 都多包了一层 stLayoutWrapper（新加的
    # width/height 布局特性用的），.st-key-home_career_role_scroll 外面这层 wrapper 默认
    # flex:0 1 auto（不会 grow），所以它自己高度塌缩成 0，里面的 role_scroll 就算自己写了
    # flex:1 也没用——父级不是真正撑开的 flex 容器，子级的 flex 属性无从谈起。用 :has() 选择器
    # 单独把这层 wrapper 也锁成 flex:1 1 0 的纵向 flex 容器，角色列表才重新正常撑开、能滚动。
    # 这是这轮改动里唯一一个"自己测出来、没等 Paul 发现"的问题，说明之前的教训（改完必须在真实
    # 浏览器里量一遍，不能只看代码逻辑）这次真的用上了。
    # 2026-10-04 十版：Paul 反馈角色多的那个分类（Business & Management，12 个）挤在 260px 里
    # 看着太局促，要求两边的框一起变大。既然上一版已经把"固定高度跟内容大小脱钩"这件事彻底修对了
    # （flex:0 0 Npx!important），这里改高度就只是单纯换个数字——260 -> 320（移动端 220 -> 260），
    # 上传框和卡片两处常量一起改，保证还是始终一样高。
    # 2026-10-04 九版：前两版（七/八版）关于卡片高度的判断都是错的——不是被 Streamlit 自己的
    # emotion class 按顺序盖掉，是直接打开 Paul 本地跑着的 app（http://localhost:8501）用浏览器
    # 开发者工具连上去，对着真实 DOM 量出来的：.st-key-home_career_card 作为纵向 flex 容器
    # （st.container 的默认行为）里的一个 flex item，Streamlit 给它的默认样式是
    # flex-basis:0%; flex-grow:1——flexbox 规则里，flex-basis 只要不是 auto，height 这个属性
    # 在主轴（纵向排列时主轴就是高度）上就直接被忽略，不管加不加 !important 都没用，容器高度完全
    # 由内容撑开（分类/角色这两步内容多，所以比 260 高；"done" 这步内容少，碰巧看起来还行，
    # 这也是为什么之前几版死活没找对根源——每次都是挑了内容多的那一步在验证）。真正的修法是把
    # flex-basis/flex-grow 一起锁死：flex:0 0 260px!important，让这个 flex item 的主轴尺寸直接
    # 定死 260px，不再跟内容大小有任何关系。这版改完之后，已经在 localhost 现场用浏览器脚本实测
    # 验证过：分类步、12 个角色的 Business & Management 角色步、选定结果步，三种形态高度都稳定
    # 在 260px，和左边上传框完全对齐。
    # 2026-10-03 五版：上一版 flex-wrap 没生效的真正原因找到了——直接翻了 Streamlit 1.50
    # 前端打包代码里 StyledFlexContainerBlock 的定义（st.container 实际渲染出来的那个 div），
    # 它默认就是 display:flex + flex-direction:column（纵向 block 嘛），而且这是用 emotion
    # 生成的 CSS class，跟我们自己 <style> 里写的 .st-key-xxx 选择器优先级一样高，谁在文档里
    # 后出现谁赢，顺序不保证。之前只写了 flex-wrap:wrap，没覆盖 flex-direction，所以容器还是
    # "纵向排列、超高才换列"，换行高度没溢出就永远不会真的"并排"——看起来就是竖排 list。
    # 这版把 display/flex-direction/flex-wrap 全部加 !important 并显式指定 row，保证一定生效。
    # 2026-10-04 六版：Paul 反馈"生成图片的时候也要左至右丝滑切换"——选完角色、插画卡片真正
    # 出现这一下，之前只给 .career-card 挂了 animation，没加 !important，保险起见也给这几条
    # 关键的切换动画全部补上 !important（跟上面 flex-wrap 踩过的坑一样，防止被 Streamlit 自己
    # emotion 生成的样式按加载顺序盖掉）。插画本身另外单独给了一个延迟 50ms 的"缩放+滑入"动画
    # （homeCareerImgIn），跟文字的滑入错开一点点，视觉上更像插画是"随后冒出来"的，而不是和文字
    # 完全贴在一起生硬地同时出现。
    # 2026-10-04 十五版：十四版去掉 hover 描边之后，Paul 又反馈"再好好看一下，感觉是图层的问题
    # 还是有遮住的情况"，截图是 Machine Learning Engineer 带着一圈圆角蓝色描边。这次直接连上
    # localhost，用 JS 真的让这个按钮进入浏览器原生 :focus 状态截图复现——确认了：这圈"描边"
    # 不是遮挡/图层 bug，就是我自己之前加的 :focus 态 box-shadow 光环（鼠标点击按钮那一下，浏览器
    # 原生就会给它 focus，不管是不是键盘操作），渲染本身没有问题，纯粹是"每次点击胶囊的瞬间都会
    # 闪一下这圈光环"这个设计在 Paul 看来显得多余、不像是正常交互反馈，容易被误读成"东西盖住了"。
    # 选中状态已经用实心蓝底表达得很清楚，没必要再加一层点击光环，这版把 :focus 的 box-shadow 去掉
    # （outline/box-shadow 都清空），改成只在 :focus-visible（真正的键盘 Tab 导航）时才显示这圈蓝
    # 光环——鼠标点击不再有这个视觉效果，但用 Tab 键在列表里切换时还是有清晰的焦点指示，无障碍可
    # 访问性不受影响。
    # 2026-10-04 十四版：Paul 反馈点 Data Scientist 的时候"边框很奇怪，像是被什么东西遮住了"。
    # 一开始怀疑是 :focus 的那圈蓝色 box-shadow 光环出了问题，直接连上 localhost 用浏览器实测
    # 排查——结果发现跟 focus/点击本身没关系：每次 fragment 重跑换一屏新的胶囊列表（分类->角色，
    # 或者点完角色跳到 done 这一步）之后，鼠标指针本身没挪地方，但它所在的那个像素位置现在换成了
    # 列表里别的、用户根本没点过的胶囊——而 :hover{border-color:#0071e3} 这条规则只要鼠标停在
    # 上面就会生效，不管是不是真的主动悬停上去的。于是经常能看到"鼠标根本没点中的那个胶囊"边框
    # 莫名其妙变蓝，跟真正表示"已选中"的蓝色实心胶囊（type=primary）或者 :focus 的蓝色光环放在
    # 一起，看着就是说不清楚的一圈奇怪描边。真正的"已选中"状态已经用实心蓝底表达得很清楚了，
    # hover 时额外描边变蓝纯属多余还会制造这种误导，这版直接去掉，hover 只保留背景色变化。
    # [十一版更新，见 render_career_picker 里 _advance() 调用处的注释]：选定角色/自定义方向
    # 这一步已经从 scope="app" 改成跟分类/角色一样的 fragment 内部重跑了——Paul 反馈即使带了
    # 滑入动画，全页重跑前那下更明显的"变暗"还是不够平滑，这次确认是重跑范围的问题并改掉，代价
    # 是 Start assessment 按钮不会随这次点击立刻解锁，见那边注释。
    # 2026-10-04 八版：加了 !important 之后卡片自己稳稳 260px 了，但 Paul 截图显示上传框反而
    # 更高——说明不是卡片矮了，是左边 stFileUploaderDropzone 自己的 height:260px 之前也没加
    # !important，没能稳定生效（同一类坑）。这版给上传框的 height 也补上 !important，顺带加了
    # min-height/max-height/box-sizing:border-box 三重保险，防止内容把框撑高。
    # 2026-10-04 七版：Paul 反馈两个问题——(1) 选定角色后卡片比左边上传框矮一截，没对齐；
    # (2) 切到插画那一步还是很卡顿。
    #   (1) 根源跟上一版 flex-wrap 的坑一样：.st-key-home_career_card 的 height:260px 跟
    #       Streamlit 自己 emotion 生成的那条 class 选择器优先级相同，谁在文档里后出现谁赢，
    #       之前没加 !important 的时候，"done" 这一步（display:block，内容天然比 260px 矮）就
    #       输给了 Streamlit 自带的 height:auto，卡片直接收缩成内容实际高度。这版给 height
    #       （含移动端媒体查询里那条）都补上 !important，保证跟上传框一样固定 260px。
    #   (2) 真正的卡顿根源找到了，不是重跑机制的问题——mascots_transparent/ 里那批抠图是直接把
    #       1254×1254 的原图扣完透明底存下来的，平均每张 1.2MB，而卡片里实际只用 150×150 显示。
    #       每次这一步渲染（不管是 fragment 内部重跑还是全页重跑）都要把这一整张 1MB+ 的图片转
    #       base64（膨胀到 ~1.5MB 文字）塞进 HTML 里传给浏览器解码，这个体量本身就会造成明显的
    #       卡顿，跟重跑方式无关。已经把这 28 张图重新缩放到 320×320（卡片 150px 显示用 2x 视网
    #       膜分辨率够用了）重新存盘，平均单张从 1.2MB 降到 ~80KB，整个目录从 35MB 降到 2MB，原图
    #       备份在 mascots_transparent_original_backup/（没改线上代码引用路径，只是以防万一）。
    css += f'''
.career-card{{position:relative;height:100%;border-radius:26px;background:{t["bg"]};display:flex;align-items:center;justify-content:space-between;gap:12px;padding:14px 14px 14px 28px;box-sizing:border-box}}
.career-card.custom{{justify-content:flex-start}}
.career-card.filled{{background:linear-gradient(135deg, color-mix(in srgb, var(--accent,#64427a) 16%, {t["bg"]}), color-mix(in srgb, var(--accent,#64427a) 6%, {t["bg"]}))}}
.career-card .career-text{{position:relative;z-index:2;min-width:0}}
.career-card .cat{{font-size:13px;color:{t["sub_text"]};opacity:.6;font-weight:500}}
.career-card .role{{font-size:21px;font-weight:700;color:var(--accent,{t["text"]});margin-top:6px;letter-spacing:-.3px;line-height:1.28;overflow-wrap:break-word}}
.career-card .mascot-bleed{{flex-shrink:0;width:280px;height:280px;max-width:56%;object-fit:contain}}
.menu-title{{font-size:15.5px;font-weight:600;margin:0 0 14px;flex-shrink:0;color:{t["text"]}}}
.menu-title .n{{opacity:.55;font-size:13px;font-weight:400}}
.menu-title.with-back{{padding-left:38px}}
.st-key-home_career_card{{position:relative;height:320px!important;flex:0 0 320px!important;border-radius:26px;overflow:hidden}}
.st-key-home_career_back_wrap{{position:absolute;top:14px;left:14px;z-index:5}}
.st-key-home_career_back_wrap button{{width:30px;height:30px;min-height:30px;padding:0;border-radius:50%;border:none;background:color-mix(in srgb, {t["bg"]} 70%, transparent);backdrop-filter:blur(6px);color:{t["text"]};font-weight:700}}
.st-key-home_career_menu_list{{display:flex!important;flex-direction:row!important;flex-wrap:wrap!important;gap:10px;align-content:flex-start}}
.st-key-home_career_menu_list [data-testid="stElementContainer"]{{width:auto!important;flex:0 0 auto!important}}
.st-key-home_career_menu_list [data-testid="stBaseButton-secondary"]{{border-radius:999px;padding:9px 18px;min-height:auto;height:auto;font-size:14px;font-weight:500;background:{t["bg"]};border:1px solid {t["border"]};color:{t["text"]}}}
.st-key-home_career_menu_list [data-testid="stBaseButton-secondary"]:hover{{background:{t["hover_bg"]}}}
.st-key-home_career_menu_list [data-testid="stBaseButton-primary"]{{border-radius:999px;padding:9px 18px;min-height:auto;height:auto;font-size:14px;font-weight:500;background:#0071e3;border:1px solid #0071e3;color:#fff}}
.st-key-home_career_menu_list [data-testid="stBaseButton-secondary"]:focus,
.st-key-home_career_menu_list [data-testid="stBaseButton-primary"]:focus,
.st-key-home_career_back_wrap button:focus{{outline:none!important;box-shadow:none!important}}
.st-key-home_career_menu_list [data-testid="stBaseButton-secondary"]:focus-visible,
.st-key-home_career_menu_list [data-testid="stBaseButton-primary"]:focus-visible,
.st-key-home_career_back_wrap button:focus-visible{{box-shadow:0 0 0 3px color-mix(in srgb, #0071e3 35%, transparent)!important}}
.st-key-home_career_card > [data-testid="stLayoutWrapper"]:has(.st-key-home_career_role_scroll){{flex:1 1 0!important;min-height:0!important;display:flex!important;flex-direction:column!important}}
.st-key-home_career_role_scroll{{overflow-y:auto!important;flex:1 1 0!important;min-height:0!important}}
.st-key-home_career_custom_link{{margin-top:auto;flex-shrink:0}}
@keyframes homeCareerSlideLR{{from{{opacity:0;transform:translateX(-22px)}}to{{opacity:1;transform:translateX(0)}}}}
@keyframes homeCareerImgIn{{from{{opacity:0;transform:scale(.92) translateX(-10px)}}to{{opacity:1;transform:scale(1) translateX(0)}}}}
.menu-title,.career-card,.st-key-home_career_menu_list,.st-key-home_career_custom_link{{animation:homeCareerSlideLR .22s cubic-bezier(.22,.9,.32,1)!important}}
.st-key-home_career_menu_list [data-testid="stElementContainer"]{{animation:homeCareerSlideLR .22s cubic-bezier(.22,.9,.32,1)!important}}
.career-card .mascot-bleed{{animation:homeCareerImgIn .32s .05s cubic-bezier(.22,.9,.32,1) backwards!important}}
@media(max-width:640px){{.st-key-home_career_card{{height:260px!important;flex:0 0 260px!important}}.career-card{{padding:14px 14px 14px 20px}}.career-card .mascot-bleed{{width:210px;height:210px}}}}
'''
    return css


def render_home_intro():
    css = _build_css(_theme_tokens())
    st.markdown(
        '<style>' + css + '</style>'
        '<div class="home-hero"><h2>Give your resume <span class="accent">direction</span>.</h2>'
        '<p>Upload your resume, choose a career path, and get your assessment.</p></div>',
        unsafe_allow_html=True)


def career_label(field):
    cat = field.get('category') or ''
    return CATEGORY_LABELS.get(cat, cat or 'Other') + ' / ' + FIELD_LABELS.get(field['id'], field['name'])


def render_career_picker(*, fields_by_id, field_categories, fields_by_category, field_options, custom_field_id):
    """苹果风格的职业路径选择卡：三步导航——先选一级分类，点进去选二级角色，选定后展示插画卡，
    每一步左上角一个返回箭头可以退回上一步。每一步只显示一层内容，所以卡片能固定成跟左边上传框
    一样的高度，不用因为"分类+角色一起铺开"而被撑高。整个选择器跑在 st.fragment 里，三步之间的
    分类浏览和返回只刷新 fragment；最终选定职业时刷新主脚本，
    让外面的 Start assessment 按钮立即重新检查启用条件。
    2026-10-04 十二版：Paul 要求去掉"自定义职业方向"这个入口（不止是样式问题，是整个功能都不要
    了），所以分类页已经不再渲染那个 tertiary 按钮。custom_field_id/is_custom 相关的参数、
    session_state 兼容逻辑、"done"步里 is_custom 分支都原样保留——不会再被 UI 触发，纯粹是为了
    不去动 app.py/scoring.py 那边依赖 is_custom_field 返回值的代码，改动范围只收在这一个文件里。
    返回 (chosen_field_id_or_None, is_custom)，但正常使用下 is_custom 现在恒为 False。"""
    chosen_key = "home_career_chosen"
    step_key = "home_career_step"  # "category" | "role" | "done"
    active_cat_key = "home_career_active_cat"
    english_to_id = {FIELD_LABELS.get(fid, fid): fid for fid in fields_by_id}

    st.session_state.setdefault(chosen_key, None)
    if step_key not in st.session_state:
        # 第一次加载：如果已经有选定结果（比如上次没刷新页面），直接进"已选定"这一步；
        # 否则从头开始，先看分类列表。
        existing = st.session_state[chosen_key]
        if existing and existing != custom_field_id:
            st.session_state[step_key] = "done"
            st.session_state[active_cat_key] = fields_by_id[existing].get("category")
        elif existing == custom_field_id:
            st.session_state[step_key] = "done"
            st.session_state[active_cat_key] = None
        else:
            st.session_state[step_key] = "category"
            st.session_state[active_cat_key] = None

    # 分类/角色两步用的是"菜单模式"外观（浅色背景、内边距、纵向排列），选定结果那一步用的是
    # 插画卡外观（渐变背景、贴边）——两种外观共用同一个 st-key-home_career_card 容器，所以每次
    # 重跑先用一段小 <style> 把容器样式切到当前这一步要的样子（跟 --accent 那个技巧一样）。
    # 颜色直接用 _theme_tokens() 算出来的字面量，不用 CSS 变量（原因见上面 _build_css 的注释）。
    t = _theme_tokens()
    MENU_MODE_CSS = (f'.st-key-home_career_card{{background:{t["bg2"]};'
                      'padding:20px 22px 14px;display:flex;flex-direction:column;box-sizing:border-box}')
    DONE_MODE_CSS = '.st-key-home_career_card{background:transparent;padding:0;display:block}'

    def _back_button():
        with st.container(key="home_career_back_wrap"):
            return st.button("←", key="home_career_back_btn")

    def _advance():
        """分类/返回这类纯内部跳转：优先只重跑 fragment 本身（st.rerun(scope="fragment")），
        不带着整页一起重跑/变灰，保证"切换要丝滑"。scope="fragment" 只有在"这次重跑本来就是
        由 fragment 内部的控件触发的"这个前提下才合法——点击分类/返回按钮正常走的就是这条路，
        但这个前提没法用本地测试工具（AppTest）复现验证，所以这里加一层保险：真遇到了不满足
        前提的边界情况，退化成全页重跑（scope="app"），而不是直接抛异常把整个页面崩掉。"""
        try:
            st.rerun(scope="fragment")
        except StreamlitAPIException:
            st.rerun(scope="app")

    @st.fragment
    def _fragment():
        chosen = st.session_state[chosen_key]
        is_custom = chosen == custom_field_id
        step = st.session_state[step_key]
        active_cat = st.session_state[active_cat_key]

        with st.container(key="home_career_card"):
            if step == "category":
                st.markdown(f'<style>{MENU_MODE_CSS}</style>', unsafe_allow_html=True)
                total_roles = sum(len(v) for v in fields_by_category.values())
                st.markdown(
                    '<div class="menu-title">🧭 Choose a category'
                    f'<span class="n">&nbsp;·&nbsp;{total_roles} career paths</span></div>',
                    unsafe_allow_html=True)
                with st.container(key="home_career_menu_list"):
                    for cat in field_categories:
                        # 返回到分类这一步时（比如从角色列表点返回），把之前选过的那个分类用
                        # 蓝色高亮（type="primary"），视觉上跟 Paul 参考图里的效果一致。
                        btn_type = "primary" if cat == active_cat else "secondary"
                        if st.button(CATEGORY_LABELS.get(cat, cat), key=f"home_cat_btn__{cat}", type=btn_type):
                            st.session_state[active_cat_key] = cat
                            st.session_state[step_key] = "role"
                            _advance()

            elif step == "role":
                st.markdown(f'<style>{MENU_MODE_CSS}</style>', unsafe_allow_html=True)
                if _back_button():
                    st.session_state[step_key] = "category"
                    _advance()
                cat_label = escape(CATEGORY_LABELS.get(active_cat, active_cat or ""))
                st.markdown(f'<div class="menu-title with-back">{cat_label}</div>', unsafe_allow_html=True)
                role_names = fields_by_category.get(active_cat, [])
                with st.container(key="home_career_role_scroll"):
                    with st.container(key="home_career_menu_list"):
                        for name in role_names:
                            field_id = field_options[name]
                            btn_type = "primary" if field_id == chosen else "secondary"
                            if st.button(FIELD_LABELS.get(field_id, name), key=f"home_role_btn__{field_id}",
                                         type=btn_type):
                                # The submit button lives outside this fragment. Re-run the
                                # app on selection so its readiness and selected field update.
                                st.session_state[chosen_key] = field_id
                                st.session_state[step_key] = "done"
                                st.rerun(scope="app")

            else:  # "done"
                st.markdown(f'<style>{DONE_MODE_CSS}</style>', unsafe_allow_html=True)
                if _back_button():
                    # 自定义方向没有"上一级角色列表"可退，直接回分类页；否则退回同一分类下的
                    # 角色列表，方便快速换一个角色而不用重新选分类。
                    st.session_state[step_key] = "category" if (is_custom or not active_cat) else "role"
                    _advance()
                if is_custom:
                    st.markdown(
                        '<div class="career-card filled custom">'
                        '<div class="career-text"><div class="cat">Custom path</div>'
                        '<div class="role">You&rsquo;ll name it below</div></div></div>',
                        unsafe_allow_html=True)
                else:
                    field = fields_by_id[chosen]
                    accent = mascot_accent_color(chosen)
                    mascot_b64 = _mascot_transparent_b64(chosen)
                    cat_display = escape(CATEGORY_LABELS.get(field.get("category", ""), field.get("category", "") or ""))
                    role_display = escape(FIELD_LABELS.get(chosen, field.get("name", "")))
                    img_html = (f'<img class="mascot-bleed" src="data:image/png;base64,{mascot_b64}">'
                                if mascot_b64 else '')
                    st.markdown(
                        f'<style>.st-key-home_career_card{{--accent:{accent};}}</style>'
                        f'<div class="career-card filled">'
                        f'<div class="career-text"><div class="cat">{cat_display}</div>'
                        f'<div class="role">{role_display}</div></div>{img_html}</div>',
                        unsafe_allow_html=True)

    _fragment()
    chosen = st.session_state[chosen_key]
    is_custom = chosen == custom_field_id
    return chosen, is_custom
