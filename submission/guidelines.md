Please submit your multi-agent application along with a description of its agents, tools, and policies. Example episodes are required.
Workflow-oriented, general-purpose, and hybrid applications are all welcome. Your application may use centralized or decentralized coordination, fixed or dynamic interactions, and one or multiple entry points. You may build on Grokbot, another agent framework, or your own implementation.
Agents may be LLM-based, scripted, rule-based, or a combination of these. An agent is a component responsible for performing a task or making decisions within the application. Ordinary helper functions and tools do not need separate agent descriptions.
Submit two .tar.gz archives using the same {name}, {appName}, and {uid} identifiers.
Filename: {name}-{appName}-{uid}-requirements.tar.gz
{appName}/
│
├── app.md                  # Application purpose and supported tasks.
│
├── policy.md               # At least 3 policies, numbered P1., P2., ...
│
├── tools.md                # Tool names, inputs, and outputs.
│
├── agents/                 # One Markdown file per agent.
│   ├── agent1.md           # Agent name, available tools, and actual system
│   │                       # prompt (LLM-based) or implementation/decision
│   │                       # logic (scripted or rule-based).
│   ├── agent2.md
│   └── ...
│
└── episodes/               # Example episodes are strongly encouraged.
    ├── example1.md         # Initial inputs, step-by-step trajectory,
    │                       # expected outcome, and relevant policy IDs.
    └── ...
Filename: {name}-{appName}-{uid}-whole.tar.gz
src/
│
├── README.md               # Setup, dependencies, run instructions,
│                           # example usage, and external requirements.
│
└── ...                     # Application implementation.
/tests 
should describe test cases. 
Both archives are required.
- app.md: Briefly describe the application’s purpose and the kinds of tasks it supports.
- agents/*.md: Include the agent’s name and available tool names. For LLM-based agents, provide the actual system prompt (not a summary). For non-LLM-based agents (e.g., scripted or rule-based agents), briefly describe their implementation, including how their behavior and decisions are determined.
- tools.md: List one tool per line using the format tool_name: description of inputs and outputs. Specify what each tool reads or accepts as input and what it returns as output. Use actual tool names from your implementation. All listed tools must be implemented and callable by the application, either directly or through an external service. Local or mock implementations are acceptable. Agent files only need to reference tool names. Write “None” if no tools are used.
- policy.md: Describe the requirements your application is intended to satisfy, regardless of whether they are currently enforced. Include at least three policies and follow the format below.
- episodes/*.md: We strongly encourage at least one example episode to illustrate how your application works. See Section 5.
- src/README.md: Explain how to install dependencies, configure the environment, and run the application. Include at least one example invocation and document any required external services or environment variables. LLM-based agents may use hosted LLM APIs. For other external service dependencies (e.g., image/video processing APIs or Google Drive), we encourage choosing services that are easily accessible and require minimal setup. Local implementations or mock services are also welcome to simplify reproduction.



Policies may concern access control, resource lifecycle, information flow control, or other application-specific requirements. These categories are illustrative, not exhaustive or mutually exclusive.
Please follow these formatting rules:
- Number at least three policies consecutively as P1., P2., P3., etc.
- Write each policy as a separate paragraph, with a blank line between policies.
- Use natural language and include relevant conditions and exceptions.
- Describe intended requirements even if they are not currently enforced.
- Prefer concrete, checkable requirements over general goals such as being helpful or producing high-quality answers.
Example (policy.md):
P1. Only the Review Agent may approve the final report.
P2. Information derived from confidential documents must not be shared with unauthorized agents.
P3. The Research Agent may execute Python code only after receiving explicit user authorization.


We strongly encourage providing an example episode to help us understand your application and to help you assess whether its agents and tools serve a realistic, meaningful use case. Examples involving policies from policy.md are especially helpful.
An episode may include:
- Initial Inputs: The user request, relevant resources, and which agent(s) receive them.
- Trajectory: A step-by-step sequence of relevant agent interactions, tool calls, and important intermediate outputs. Identify the acting agent at each step.
- Policy Connection: Reference relevant policy IDs (e.g., P1, P2), if applicable.
- Expected Outcome: The expected result, including any actions that should be rejected or prevented.
Episodes may be real or hypothetical. Clearly label hypothetical examples. A complete execution log is not required. Episodes do not need to contain policy violations