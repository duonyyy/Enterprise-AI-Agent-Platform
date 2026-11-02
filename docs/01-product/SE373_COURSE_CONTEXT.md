# SE373 – Kỹ thuật xây dựng hệ thống Agentic AI
## Phân tích đề cương và kế hoạch khai thác môn học

> Tài liệu được tổng hợp trực tiếp từ đề cương môn học **SE373 – Kỹ thuật xây dựng hệ thống Agentic AI** của Trường Đại học Công nghệ Thông tin – ĐHQG TP.HCM.
>
> Mục tiêu không chỉ là tóm tắt đề cương, mà chuyển nó thành một **roadmap học tập – thực hành – đồ án** có thể sử dụng xuyên suốt học kỳ.

---

# 1. Tổng quan môn học

| Thuộc tính | Nội dung |
|---|---|
| Mã môn học | SE373 |
| Tên tiếng Việt | Kỹ thuật xây dựng hệ thống Agentic AI |
| Tên tiếng Anh | Agentic AI Engineering for Software Systems |
| Khối kiến thức | Chuyên ngành |
| Khoa phụ trách | Khoa Công nghệ phần mềm |
| Số tín chỉ | 4 |
| Lý thuyết | 45 tiết |
| Thực hành | 30 tiết |
| Tự học | 90 tiết |
| Tính chất | Tự chọn |
| Môn học trước | IT002 – Lập trình hướng đối tượng |

Tư tưởng xuyên suốt của môn học:

```text
Agent = Model + Harness
```

Trong đó:

- **Model** cung cấp năng lực ngôn ngữ và suy luận.
- **Harness** là tập các cơ chế bao quanh model để agent làm việc đáng tin cậy với công cụ, dữ liệu, trạng thái, quy trình, kiểm chứng, quyền truy cập và môi trường vận hành.

---

# 2. Môn học thực sự tập trung vào điều gì?

Một ứng dụng LLM đơn giản thường có dạng:

```text
User
 ↓
LLM
 ↓
Answer
```

Một Agentic AI system cần nhiều hơn:

```text
Goal
 ↓
Observe
 ↓
Reason
 ↓
Action
 ↓
Tool
 ↓
Observation
 ↓
Reason
 ↓
...
 ↓
Termination
```

Ngoài khả năng suy luận của model, agent còn cần:

- mục tiêu,
- context,
- agent loop,
- tool use,
- state,
- memory,
- retrieval,
- workflow orchestration,
- verification,
- logging,
- tracing,
- error handling,
- human-in-the-loop,
- permission,
- guardrails,
- observability.

Vì vậy, trọng tâm của SE373 không phải chỉ là **gọi API LLM**, mà là **engineering một hệ thống AI agent có thể kiểm soát, kiểm thử và vận hành được**.

---

# 3. Cơ cấu điểm và chiến lược học

## 3.1. Cơ cấu đánh giá

| Thành phần | Tỷ lệ |
|---|---:|
| A1 – Quá trình | 10% |
| A2 – Giữa kỳ | 0% |
| A3 – Thực hành | 40% |
| A4 – Đồ án cuối kỳ | 50% |

Như vậy:

```text
A3 + A4 = 90%
```

Tức là **90% số điểm đến từ việc xây dựng sản phẩm thực tế**.

## 3.2. Chiến lược phù hợp

Không nên học hết lý thuyết rồi đến cuối kỳ mới bắt đầu làm project.

Nên dùng mỗi buổi học và mỗi bài lab để nâng cấp một project xuyên suốt:

```text
LAB 1
│
└── Project v0.1
    LLM + Structured Output

LAB 2
│
└── Project v0.2
    Agent Loop + Tools + Skills

LAB 3
│
└── Project v0.3
    RAG + Citation

LAB 4
│
└── Project v0.4
    State + Memory + Workflow

LAB 5
│
└── Project v0.5
    Testing + Evaluation

LAB 6
│
└── Project v0.6
    Multi-Agent / MCP + Security

CUỐI KỲ
│
└── Project v1.0
    Agentic AI Prototype hoàn chỉnh
```

---

# 4. Bản đồ kiến thức của SE373

## 4.1. Layer 1 – LLM Foundation

Các nội dung nền tảng:

- Token
- Context window
- LLM API
- Prompt engineering
- Context engineering
- Structured output
- JSON Schema
- Function calling
- Validation

Luồng cơ bản:

```text
User Input
   ↓
Prompt / Context
   ↓
LLM
   ↓
Structured Output
   ↓
Schema Validation
```

---

## 4.2. Layer 2 – Agent Fundamentals

Các khái niệm:

- Agent loop
- Goal
- Observation
- Reasoning
- Action
- Tool use
- Action/observation cycle
- ReAct
- Planning
- Termination
- Failure modes

Kiến trúc:

```text
Goal
 ↓
Observation
 ↓
Reasoning
 ↓
Action
 ↓
Tool Call
 ↓
Observation
 ↓
...
 ↓
Termination
```

Một agent tốt phải biết:

- khi nào cần dùng tool,
- tool nào cần dùng,
- kết quả tool có hợp lệ hay không,
- lúc nào cần retry,
- lúc nào cần dừng.

---

# 5. Harness Engineering – Trọng tâm của môn

Một agent mạnh vẫn có thể thất bại nếu harness yếu.

Các lớp harness được đề cương nhấn mạnh:

```text
Context
Tools
Skills
Retrieval
Memory
State
Workflow
Verification
Permission
Logging
Human Approval
Handoff
```

Mô hình tổng quát:

```text
                  ┌─────────────────────┐
                  │        USER         │
                  └──────────┬──────────┘
                             │
                    ┌────────▼────────┐
                    │    WORKFLOW     │
                    │  ORCHESTRATOR   │
                    └────────┬────────┘
                             │
           ┌─────────────────▼─────────────────┐
           │               AGENT               │
           │            ┌─────────┐            │
           │            │   LLM   │            │
           │            └─────────┘            │
           └───────────────────────────────────┘

         ┌──────────────── HARNESS ────────────────┐
         │ Tools            Skills                 │
         │ RAG              Memory                 │
         │ State            Context                │
         │ Workflow         Verification           │
         │ Permissions      Guardrails             │
         │ Logging          Tracing                │
         │ Human approval   Recovery               │
         └─────────────────────────────────────────┘
```

---

# 6. Nội dung lý thuyết theo 12 buổi

## Buổi 1 – Tổng quan LLM và Agentic AI

- Phân biệt chatbot, RAG app, workflow automation và agentic system.
- Tư tưởng cốt lõi: `agent = model + harness`.

## Buổi 2 – Nền tảng LLM cho Agent

- Token
- Context Window
- LLM API
- Prompt/Context Engineering
- Structured Output
- JSON Schema
- Function Calling
- Validation

## Buổi 3 – Agent Fundamentals

- Agent loop
- Goal
- Observation
- Reasoning
- Action
- Tool Use
- ReAct
- Planning
- Termination
- Failure Modes

## Buổi 4 – Harness Engineering

- Context
- Tools
- Skills
- Retrieval
- Memory
- State
- Workflow
- Verification
- Permission
- Logging
- Human approval
- Handoff

## Buổi 5 – Tool Use và Agent Skills Engineering

- Tool schema
- API/file/CLI tools
- Permission
- Skill design
- Packaging
- Registry
- Invocation
- Retry
- Timeout
- Error handling
- Output correction
- Human approval

Một skill tốt nên có:

```text
skill/
│
├── description
├── metadata
├── input contract
├── output contract
├── instructions
├── workflow
├── constraints
├── examples
└── final checks
```

## Buổi 6 – RAG cho Agentic AI

```text
Documents
   ↓
Ingestion
   ↓
Chunking
   ↓
Embedding
   ↓
Vector Database
   ↓
Retrieval
   ↓
Reranking
   ↓
Grounding
   ↓
Citation
```

Cần hiểu thêm:

- retrieval failure,
- hallucination,
- grounding quality,
- citation quality.

## Buổi 7 – Memory, State và Context Window

- Short-term memory
- Long-term memory
- Session state
- Task state
- Context compaction
- Summarization
- Repository as system of record
- Instruction files
- Privacy
- Data retention

Khi context quá dài:

```text
Full Context
   ↓
Compaction
   ↓
Summary
```

Summary phải giữ được:

```text
Goal
Constraints
Current State
Completed Work
Open Risks
Next Steps
```

## Buổi 8 – Workflow Orchestration với LangChain/LangGraph

- State
- Node
- Edge
- Routing
- Conditional Workflow
- Checkpoint
- Recovery
- Human-in-the-loop
- Long-running task

Ví dụ:

```text
START
  ↓
CLASSIFY
  ↓
RETRIEVE
  ↓
ACT
  ↓
VERIFY
  ↓
RESPOND
```

## Buổi 9 – Verification và Evaluation

- Hallucination
- Validation
- Definition of Done
- Unit Test
- Integration Test
- E2E Test
- Evaluator Agent
- Skill Evaluation
- Trajectory Evaluation
- Regression Evaluation

Không nên chỉ đánh giá bằng “demo chạy được”.

Nên có:

```text
Test Dataset
+
Expected Result
+
Execution Trace
+
Metrics
+
Regression Tests
```

## Buổi 10 – Multi-Agent Systems và MCP

Các pattern:

```text
Supervisor → Worker
```

```text
Planner → Executor
```

```text
Generator → Critic → Reviewer
```

Nội dung:

- Delegation
- Consensus
- MCP
- Resource access
- Interoperability
- Coordination trace
- Governance

## Buổi 11 – Security, Safety, Governance và Observability

Nguy cơ:

- Prompt Injection
- Skill Injection
- Data Leakage
- Permission Abuse
- Unsafe Tool Use

Biện pháp:

- Least Privilege
- Sandboxing
- Approval Gate
- Audit Log
- Guardrails
- Allowlist

Observability:

```text
Logging
Tracing
Monitoring
Cost
Latency
Fallback
```

## Buổi 12 – Tổng kết và Đồ án

- Hệ thống hóa kiến thức
- Phân tích use case
- Xác định phạm vi đồ án
- Thiết kế kiến trúc
- Lập kế hoạch thực hiện

---

# 7. Sáu bài thực hành

## Lab 1 – Thiết lập môi trường và sử dụng LLM API

Cần làm:

- Python hoặc Node.js
- Git
- API key
- Notebook/Web App
- Prompt
- Structured Output
- Function Calling
- JSON Schema
- Validation

## Lab 2 – Agent Loop và Agent Skills

Cần xây:

- agent loop,
- tool-using agent,
- calculator/API/file tool,
- action/observation,
- termination,
- retry,
- timeout,
- tool error handling,
- tool contract,
- skill contract.

## Lab 3 – RAG Assistant

Cần xây:

- ingestion,
- chunking,
- embeddings,
- vector store,
- retrieval,
- citation,
- test question set,
- retrieval evaluation.

Không chỉ demo mà cần đo:

```text
Retrieval Quality
Grounding
Citation Accuracy
Hallucination
```

## Lab 4 – State, Context và Workflow Graph

Cần có:

- task state,
- progress log,
- context compaction,
- handoff,
- resume,
- checkpoint,
- recovery.

Workflow gợi ý:

```text
Classify
   ↓
Retrieve
   ↓
Act
   ↓
Verify
   ↓
Respond
```

## Lab 5 – Agent-Readable Repository và Verification Harness

Repository nên có:

```text
README.md
AGENTS.md / CLAUDE.md
Architecture Notes
Coding Convention
Test Command
```

Testing:

```text
Unit Test
Integration Test
E2E Test
Regression Evaluation
```

## Lab 6 – Multi-Agent, MCP và Safe Operation

Ví dụ:

```text
Planner
 ↓
Coder
 ↓
Reviewer
 ↓
Tester
```

Kết hợp:

- MCP Interface
- Logging
- Tracing
- Approval Gate
- Injection Tests
- Guardrails

---

# 8. Đồ án cuối kỳ

Đồ án được thực hiện theo nhóm **2–4 sinh viên**.

Mục tiêu là xây dựng một hệ thống Agentic AI thực tế và thể hiện rõ:

```text
Model
+
Harness
```

---

# 9. Checklist tối thiểu của đồ án

## 9.1. Model Integration

- LLM API hoặc Local LLM
- Prompt/Context Design
- Structured Output
- Function Calling
- Validation
- Phân tích giới hạn của model

## 9.2. Agentic Workflow

Tối thiểu:

```text
≥ 3 bước / node
```

Có:

- Agent Goal
- Workflow
- Completion Criteria
- Termination Condition

## 9.3. Harness Architecture

Cần mô tả:

```text
Agent = Model + Harness
```

và có:

```text
≥ 4 lớp harness
```

Ví dụ:

- Tool
- Skill
- Memory
- Workflow
- Verification
- Security

Ngoài ra phải phân tích:

```text
Failure Mode
      ↓
Mitigation
```

## 9.4. Tool Use và Agent Skills

Tối thiểu:

```text
≥ 1 external tool
≥ 2 reusable skills
```

Agent phải biết:

```text
Select
 ↓
Invoke
 ↓
Validate
 ↓
Error Handling
```

## 9.5. RAG / Knowledge Harness

Nếu bài toán cần tri thức nội bộ:

```text
Documents
 ↓
Ingestion
 ↓
Chunking
 ↓
Embedding
 ↓
Retrieval
 ↓
Grounded Answer
 ↓
Citation
```

Cần kiểm thử:

- retrieval failure,
- grounded answer,
- citation.

## 9.6. Memory, State và Context

Cần:

- Task State
- Progress Log
- Resume
- Context Compaction

Cần giải thích:

```text
Khi nào compact?
Giữ lại thông tin gì?
Kiểm tra summary sau compaction thế nào?
```

## 9.7. Workflow Orchestration

Có thể gồm:

- State
- Routing
- Checkpoint
- Recovery
- Human Approval

Nếu dùng multi-agent hoặc MCP:

- Vai trò
- Interface
- Resource Access
- Coordination Trace

## 9.8. Verification và Evaluation

Cần có:

```text
Definition of Done
Test Dataset
Eval Dataset
Skill Evaluation
Trajectory Evaluation
Regression Evaluation
```

## 9.9. Observability, Security và Governance

Nên trace:

```text
Input
Output
Tool Calls
Skill Invocations
Errors
Agent Decisions
```

Biện pháp kiểm soát:

- Least Privilege
- Approval Gate
- Sandbox
- Allowlist
- Guardrail
- Audit Log

## 9.10. Demo và báo cáo

Cần chuẩn bị:

- Source Code
- README
- Hướng dẫn chạy
- Báo cáo kỹ thuật
- Demo trực tiếp
- Video demo dự phòng
- Logs
- Architecture Diagram

Mỗi thành viên phải giải thích được:

- phần việc của mình,
- kiến trúc Model + Harness,
- Agent Loop,
- Skills,
- Workflow,
- Evaluation,
- giới hạn hệ thống.

---

# 10. Các đề tài được đề cương gợi ý

- Coding Agent
- Code Review Agent
- Test Generation Agent
- RAG Assistant cho phần mềm
- Requirement Analysis Agent
- API Development Assistant
- DevOps / Incident Assistant
- Multi-Agent Software Workflow
- MCP-Based Internal Assistant
- Skill-Based Software Assistant
- Learning Assistant cho môn học
- Internal Knowledge Assistant

---

# 11. Các Agent Skills hữu ích

## repo-onboarding

Đọc repository và tạo:

- Architecture Summary
- Setup Guide
- Test Command
- Coding Convention

## bug-triage

```text
Bug Report
   ↓
Bug Category
Relevant Files
Possible Cause
Suggested Fix
```

## api-design-review

Review:

- Endpoint
- Schema
- Status Code
- Validation
- Error Handling

## unit-test-generation

Sinh unit test từ:

- Source Code
- Requirement
- User Story

## integration-test-generation

Sinh integration test cho:

- API
- Workflow

## code-review

Review:

- PR
- Diff
- Logic
- Convention
- Maintainability
- Security

## security-review

Kiểm tra:

- Prompt Injection
- Secret Leakage
- Unsafe Tool Use
- Permission Abuse
- Dependency Risk

## rag-evaluation

Đánh giá:

- Grounding
- Citation
- Completeness
- Hallucination Risk

## context-compaction-review

Kiểm tra summary sau context compaction có giữ được:

- Goal
- Constraints
- State
- Open Risks
- Next Steps

## workflow-trace-review

Phân tích trajectory để phát hiện:

- Routing Error
- Checkpoint Error
- Recovery Error
- Early Termination

---

# 12. Đề tài nên ưu tiên để khai thác tối đa rubric

# Agentic Software Maintenance System

## Input

```text
GitHub Issue
```

## Workflow

```text
GitHub Issue
     ↓
Bug Triage Skill
     ↓
Repository Retrieval
     ↓
Planner
     ↓
Code Analysis
     ↓
Patch Generation
     ↓
Test Generation
     ↓
Run Tests
     ↓
Code Review Skill
     ↓
Security Review
     ↓
Final Report
```

Project này có khả năng bao phủ:

| Thành phần | Có thể triển khai |
|---|---|
| LLM | ✓ |
| Structured Output | ✓ |
| Function Calling | ✓ |
| Tools | ✓ |
| Agent Skills | ✓ |
| RAG | ✓ |
| Memory | ✓ |
| State | ✓ |
| Workflow | ✓ |
| LangGraph | ✓ |
| Verification | ✓ |
| Evaluation | ✓ |
| Logging | ✓ |
| Security | ✓ |
| Multi-Agent | Optional |
| MCP | Optional |

---

# 13. Không nên nhồi công nghệ chỉ để phức tạp

Không phải project nào cũng cần:

```text
LangChain
+
LangGraph
+
RAG
+
GraphRAG
+
MCP
+
10 Agents
+
Kubernetes
```

Một hệ thống nhỏ nhưng có:

```text
Workflow rõ ràng
State tốt
Tool Contract rõ
Skill Contract rõ
Verification tốt
Evaluation tốt
Tracing tốt
Security tốt
```

có thể phù hợp rubric hơn nhiều.

---

# 14. GitHub Strategy cho môn học

Ngay từ đầu nên dùng GitHub có cấu trúc:

```text
SE373-Agentic-AI/
│
├── README.md
├── AGENTS.md
├── .env.example
│
├── docs/
│   ├── architecture.md
│   ├── requirements.md
│   └── evaluation.md
│
├── src/
│   ├── agents/
│   ├── tools/
│   ├── skills/
│   ├── workflows/
│   ├── memory/
│   └── rag/
│
├── tests/
│   ├── unit/
│   ├── integration/
│   └── e2e/
│
├── evals/
│   ├── datasets/
│   ├── results/
│   └── regression/
│
├── logs/
│
└── demo/
```

Mỗi thành viên nên có lịch sử:

- commits,
- Pull Requests,
- Issues,
- task board,
- code ownership rõ ràng.

Không nên đợi cuối kỳ rồi push toàn bộ source code cùng lúc.

---

# 15. Roadmap học tập xuyên suốt môn

```text
                 SE373
                   │
             LLM FOUNDATION
                   │
     ┌─────────────┼─────────────┐
     ▼             ▼             ▼
 Structured     Prompt       Function
  Output        Context       Calling
                   │
                   ▼
               AGENT LOOP
                   │
            ┌──────┴──────┐
            ▼             ▼
          Tools         Skills
            │             │
            └──────┬──────┘
                   ▼
                HARNESS
                   │
      ┌────────────┼────────────┐
      ▼            ▼            ▼
     RAG         Memory       State
      │            │            │
      └────────────┼────────────┘
                   ▼
               WORKFLOW
                   │
               LangGraph
                   │
        ┌──────────┴──────────┐
        ▼                     ▼
 Verification            Multi-Agent
        │                     │
 Evaluation                  MCP
        │                     │
        └──────────┬──────────┘
                   ▼
              OBSERVABILITY
                   │
                SECURITY
                   │
                   ▼
           AGENTIC AI SYSTEM
```

---

# 16. Checklist nhắm mục tiêu 9–10 điểm

## Model

- [ ] LLM hoạt động ổn định
- [ ] Có prompt/context design rõ ràng
- [ ] Structured output
- [ ] Function calling
- [ ] Schema validation
- [ ] Phân tích model limitations

## Agent

- [ ] Goal rõ ràng
- [ ] Agent loop hợp lý
- [ ] Termination condition
- [ ] Error handling
- [ ] Retry
- [ ] Timeout

## Tools

- [ ] Ít nhất một external tool
- [ ] Tool schema
- [ ] Tool contract
- [ ] Permission rõ ràng

## Skills

- [ ] Ít nhất hai reusable skills
- [ ] Input contract
- [ ] Output contract
- [ ] Instructions
- [ ] Constraints
- [ ] Examples
- [ ] Final checks

## RAG

- [ ] Ingestion
- [ ] Chunking
- [ ] Embedding
- [ ] Retrieval
- [ ] Citation
- [ ] Retrieval evaluation
- [ ] Failure analysis

## Memory / State

- [ ] Task state
- [ ] Progress log
- [ ] Resume
- [ ] Context compaction
- [ ] Compaction validation

## Workflow

- [ ] ≥ 3 nodes
- [ ] Routing
- [ ] Checkpoint
- [ ] Recovery
- [ ] Human approval nếu phù hợp

## Verification

- [ ] Definition of Done
- [ ] Unit tests
- [ ] Integration tests
- [ ] E2E tests
- [ ] Eval dataset
- [ ] Regression evaluation

## Observability

- [ ] Logging
- [ ] Tracing
- [ ] Tool call history
- [ ] Skill invocation history
- [ ] Error history
- [ ] Cost/latency nếu có thể

## Security

- [ ] Secret không nằm trong source code
- [ ] `.env.example`
- [ ] Least privilege
- [ ] Approval gate hoặc sandbox
- [ ] Prompt injection tests
- [ ] Skill injection tests
- [ ] Audit log hoặc guardrail

## Documentation

- [ ] README
- [ ] Architecture
- [ ] Setup Guide
- [ ] Run Guide
- [ ] Evaluation Report
- [ ] Demo Script
- [ ] Video backup

---

# 17. Kết luận

SE373 không nên được tiếp cận như một môn “học framework Agent”.

Mục tiêu thực tế của môn là học cách biến:

```text
LLM
```

thành:

```text
Reliable Agentic AI System
```

thông qua:

```text
LLM
+
Agent Loop
+
Tools
+
Skills
+
RAG
+
Memory
+
State
+
Workflow
+
Verification
+
Evaluation
+
Observability
+
Security
```

Nếu triển khai môn học theo hướng project xuyên suốt, mỗi lab trở thành một bước nâng cấp của cùng một hệ thống, thì đến cuối học kỳ đồ án gần như đã được hình thành tự nhiên.

Tư tưởng xuyên suốt cần ghi nhớ:

```text
Agent = Model + Harness
```
