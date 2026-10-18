# 文稿检查系统

面向项目交付文档的本地检查工具。系统支持 DOC/DOCX/PDF 导入，并结合文档格式、合同基准和段落上下文发现可能的问题，适合在交付前做批量初检和人工复核。

## 功能

- 页码检查：正文从 1 开始、连续性、跳号/重复、`X/X 页`、页脚总页数、目录页码与正文映射。
- 格式检查：正文样式、标题层级、字体和字号等可配置基线。
- 标点检查：重复标点、段末标点、表名/小标题和省略号等上下文豁免。
- 合同一致性：项目名称、合同金额、付款节点、甲乙方/委托方角色和服务对象语境。
- 阶段逻辑：根据合同时间线识别“当前进度尚未达到”的上线、验收和运行表述；里程碑或进度计划中的目标表述会单独处理。
- 上下文评分：长期合同记忆与当前段落短期记忆交叉验证，给每个候选问题生成可解释的错误风险分数，并支持按风险、严重程度、页码和问题类型排序。
- 批量处理：按问题类型批量确认、忽略或修复，支持导出检查报告和修复前备份。
- 可选 JEV/TypeSafe：对规则候选进行批量语义复核；未配置远程模型时使用本地启发式评分。

## 工作流程

1. 启动本地服务并上传合同及待检查文档。
2. 在页面 1 确认合同基准信息和项目时间线。
3. 运行检查，查看原文、问题描述、证据片段和错误风险分数。
4. 按风险或问题类型批量处理，人工确认后再执行修复。
5. 导出报告；自动修复前会保留备份。

## 环境要求

- Python 3.10 或更高版本。
- macOS 上的 `.doc` 转换和系统 OCR 依赖系统自带的 `textutil`、Vision；没有这些能力时仍可检查 DOCX 和文字型 PDF。
- 可选安装 WPS 或 LibreOffice，以提高 Word/PDF 转换兼容性。

## 安装与启动

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
./run.sh
```

需要可复现的运行环境时，可使用已验证版本锁定文件：

```bash
python -m pip install -r requirements.lock
```

启动后打开 <http://127.0.0.1:8600>。

也可以直接运行：

```bash
python -m doccheck.webapp.app
```

## JEV/TypeSafe 配置

系统默认使用本地上下文启发式评分。配置兼容接口后，系统会把规则候选批量发送给模型，并将模型结果与本地规则、长期合同基准和当前段落上下文合并。

评分是用于排序和人工复核的风险分数，不是经过统计校准的法律结论；高分表示证据更充分，最终判断仍应由使用者确认。

不要将真实密钥写入配置文件、代码或 Git。启动前通过环境变量配置：

```bash
export TYPESAFE_API_KEY="你的密钥"
export JEV_BASE_URL="https://api.typesafe.ai/v1"
export JEV_MODEL="jev-latest"
export JEV_ENABLED="true"
./run.sh
```

变量示例见 `.env.example`。当前适配器使用 TypeSafe Jev 的兼容端点；具体模型和接口权限以服务商文档为准。

## 配置与运行数据

- `config.yaml`：公开的空白配置模板，保存合同基准、时间线、规则白名单和 JEV 默认参数。
- `workdir/`：上传文件、会话、上下文截图和导出报告，运行时自动创建。
- `backups/`：自动修复前的备份，修复时自动创建。

真实合同、上传文档、修复备份、运行日志、API 密钥和 `graphify-out/` 均不应提交到公开仓库。

## 测试

```bash
python tests/test_page_rules.py
python tests/test_probability_heuristic.py
python tests/test_typesafe_adapter.py

# 安装 requirements-dev.txt 后运行全部 pytest 测试
python -m pytest -q
```

测试使用合成数据，不依赖个人合同或本机文档目录。

## 目录结构

```text
doccheck/
  rules/              检查规则
  webapp/             Flask 页面、模板和静态资源
  docloader.py        DOCX/PDF 统一加载
  probability.py      上下文与 JEV 概率评分
  context.py          长期合同与短期段落上下文
  engine.py           检查调度
  repair.py           DOCX 自动修复与回滚
run.sh                启动脚本
config.yaml           默认配置模板
requirements.txt      Python 依赖
tests/                可移植回归测试
```

## 开源许可

本项目采用 [MIT License](LICENSE)。使用真实业务文档时，请自行确认数据保护、合同保密和第三方服务合规要求。
