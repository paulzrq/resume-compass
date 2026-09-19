# 简历罗盘 · 本地版

上传简历 → 选择目标领域 → AI按「简历罗盘」框架逐维度打分 → 自动生成官方成绩单风PDF报告，
报告会自动存进上一级的 reports 文件夹。

## 第一次使用前的准备（只需要做一次）

1. 如果你的电脑装过Anaconda，终端提示符前面通常会自动带一个 `(base)`。先执行一次，避免它跟下面的虚拟环境打架：
   ```
   conda deactivate
   ```

2. 建一个独立的虚拟环境（避免污染系统Python，也避免权限问题）：
   ```
   cd ~/Documents/resume_evaluation/app
   python3 -m venv venv
   source venv/bin/activate
   ```
   激活成功后，终端提示符前面会出现 `(venv)`。
   如果这一步报 `[Errno 1] Operation not permitted`，是 macOS 的 Documents 文件夹隐私保护拦住了终端的写入权限：去 系统设置 → 隐私与安全性 → 文件和文件夹（或"完全磁盘访问权限"），把你用的终端程序对"文稿"（Documents）的访问打开，然后重新开一个终端窗口再试。

3. 装依赖：
   ```
   pip install -r requirements.txt
   ```
   （`requirements.txt` 里的版本号是已经踩过坑、验证能装成功的组合，不用再自己调整）

4. 准备一个 Anthropic API Key（用于调用AI打分，费用极低，一份简历大概几分钱美元）：
   打开 https://console.anthropic.com/settings/keys 注册/登录后创建一个 Key（`sk-ant-...`开头），**创建出来立刻复制**——这个页面只在刚创建那一刻显示完整值，关掉之后就再也看不到完整的了，只能重新建一个。
   这个 Key 只在你自己电脑上使用，填在软件界面里，不会被保存到磁盘或上传到任何地方。

## 以后每次要用（日常启动，不用重复上面的安装步骤）

```
cd ~/Documents/resume_evaluation/app
conda deactivate          # 如果终端提示符前面出现了 (base) 再执行这一句，没有就跳过
source venv/bin/activate  # 提示符前面出现 (venv) 才算激活成功
streamlit run app.py
```
会自动打开浏览器界面（默认 http://localhost:8501）。左侧栏填入 API Key，
上传一份简历PDF，选目标领域，点击「开始评估」即可。用完了想关掉，回终端按 `Ctrl+C`，
再 `deactivate` 退出虚拟环境（不退出也没关系，下次开新终端会是干净状态）。

## 文件说明

- `app.py` — 界面主程序
- `scoring.py` — 打分逻辑：读取上级目录的 `framework.json`、拼装prompt、调用API、
  计算加权总分（模型只负责判断1-5分，加权算术由代码确定性计算，避免模型算错）
- `report.py` — PDF报告生成（纯 reportlab，不依赖浏览器/系统字体，内嵌了中文字体）
- `fonts/DroidSansFallbackFull.ttf` — 内嵌中文字体，报告能在任何电脑上正常显示中文
- `sample_report_Jimmy.pdf` — 用测试数据生成的示例，只是给你看效果，不是真实评估结果

## 已知限制 / 后续可以做的事

- 目前一次只评估一个目标领域；如果想比较同一学生在两个领域的表现，分别跑两次即可
- 扫描版PDF（图片形式的简历）提取不出文字，暂不支持，需要文字版PDF
- 加分项由AI判断简历中是否有明确证据，会保守一些（宁可不勾选，也不臆测）
- 如果以后想让同事也能用，需要把这个本地脚本换成有登录/多用户的部署方案，目前是单人本地使用的版本
