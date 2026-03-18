# LC Architecture Diagram

## Component Overview

```mermaid
graph TB
    subgraph "Main Entry Point"
        A[main()] --> B[Parse CLI Args]
        B --> C[Initialize LCClient]
        C --> D[Load System Prompt]
        D --> E[Initialize PromptSession]
        E --> F[Initialize ToolRegistry]
        F --> G[Start Interactive Loop]
    end
    
    subgraph "LCClient"
        H[LCClient]
        H --> H1[api_key]
        H --> H2[client OpenAI]
        H --> H3[messages history]
        H --> H4[model selection]
        H --> H5[token tracking]
    end
    
    subgraph "ToolRegistry"
        I[ToolRegistry]
        I --> I1[math]
        I --> I2[current_time]
        I --> I3[write_file]
        I --> I4[run_command]
        I --> I5[glob]
        I --> I6[grep]
        I --> I7[read_file]
        I --> I8[list_directory]
    end
    
    G --> H
    G --> I
```

## Interactive Chat Flow

```mermaid
flowchart TD
    Start([User Input]) --> IsCommand{Starts with /?}
    
    IsCommand -->|Yes| CommandHandler[Command Handler]
    CommandHandler --> ParseCmd{Command Type}
    ParseCmd -->|/help| ShowHelp[Display Help]
    ParseCmd -->|/tools| ListTools[Show Tool Registry]
    ParseCmd -->|/model| SelectModel[Interactive Model Select]
    ParseCmd -->|/prompt| SetPrompt[Set System Prompt]
    ParseCmd -->|/clear| ClearHistory[Clear Messages]
    ParseCmd -->|/exit| Exit[Exit Program]
    
    IsCommand -->|No| ChatFlow[Chat Flow]
    
    ChatFlow --> AddUserMsg[Add User Message]
    AddUserMsg --> StreamChat[stream_chat with tools]
    StreamChat --> BuildSystem[_build_system_prompt]
    BuildSystem --> GetEnv[Get Environment Info]
    GetEnv --> GitInfo[Git Status Optional]
    GitInfo --> APIRequest[OpenAI API Request]
    
    APIRequest --> StreamResponse{Stream Response}
    StreamResponse --> ContentChunk[Content Chunks]
    StreamResponse --> ToolCall{Tool Calls?}
    
    ContentChunk --> LiveRender[Live Markdown Render]
    
    ToolCall -->|No| EndTurn[End Turn]
    ToolCall -->|Yes| CollectCalls[Collect Tool Calls]
    
    CollectCalls --> ApprovalCheck{Needs Approval?}
    ApprovalCheck -->|Yes| UserPrompt[Prompt User y/N]
    ApprovalCheck -->|No| ExecuteTool[Execute Tool]
    
    UserPrompt -->|Approved| ExecuteTool
    UserPrompt -->|Denied| DenyTool[Deny Tool Execution]
    
    ExecuteTool --> ToolResult[Get Tool Result]
    DenyTool --> DenyResult[Denial Message]
    
    ToolResult --> AddToolMsg[Add Tool Response to Messages]
    DenyResult --> AddToolMsg
    
    AddToolMsg --> CallModel[Call Model Again]
    CallModel --> StreamResponse
    
    EndTurn --> CalcStats[Calculate Tokens/sec]
    CalcStats --> LoopBack[Return to Input]
    LiveRender --> EndTurn
    
    ShowHelp --> LoopBack
    ListTools --> LoopBack
    SelectModel --> LoopBack
    SetPrompt --> LoopBack
    ClearHistory --> LoopBack
    Exit --> End([End])
```

## Class Structure

```mermaid
classDiagram
    class LCClient {
        +api_key str
        +client OpenAI
        +model str
        +system_prompt str
        +messages List[Dict]
        +prompt_tokens int
        +completion_tokens int
        +list_models() List[str]
        +stream_chat(message, tools) Generator
        +_build_system_prompt() str
        +_stream(tools) Generator
    }
    
    class ToolRegistry {
        +tools Dict
        +requires_approval Set
        +needs_approval(tool_name) bool
        +execute_tool(tool_name, args) str
        +_write_file(args) str
        +_run_command(args) str
        +_glob(args) str
        +_grep(args) str
        +_read_file(args) str
        +_list_directory(args) str
    }
    
    class main {
        +argparse CLI parsing
        +PromptSession UI
        +Interactive loop
        +Slash command handling
        +Live markdown rendering
    }
    
    main --> LCClient
    main --> ToolRegistry
    LCClient ..> ToolRegistry : passes tools to API
```

## Data Flow

```mermaid
sequenceDiagram
    participant U as User
    participant M as main()
    participant C as LCClient
    participant T as ToolRegistry
    participant A as OpenAI API
    
    U->>M: Input message
    M->>M: Check for /command
    alt Is slash command
        M->>M: Execute command handler
        M->>U: Show result
    else Regular message
        M->>C: stream_chat(message, tools)
        C->>C: _build_system_prompt()
        C->>A: POST /chat/completions
        A->>C: Stream chunks
        
        loop For each chunk
            C->>M: Yield chunk
            M->>M: Render markdown live
            alt Has tool call
                M->>C: Collect tool call data
            end
        end
        
        alt Model requested tool
            M->>T: needs_approval(tool_name)?
            alt Requires approval
                M->>U: Prompt for approval
                U->>M: y/N response
                alt Approved
                    M->>T: execute_tool(name, args)
                    T->>T: Run tool function
                    T->>M: Return result
                    M->>U: Show result
                else Denied
                    M->>M: Add denial message
                end
            else No approval needed
                M->>T: execute_tool(name, args)
                T->>M: Return result
                M->>U: Show result
            end
            M->>C: _stream() with tool results
            C->>A: POST /chat/completions
            A->>C: Final response
        end
    end
```

## Tool Execution Flow

```mermaid
flowchart LR
    subgraph "Tools Requiring Approval"
        WF[write_file]
        RC[run_command]
    end
    
    subgraph "Tools Without Approval"
        M[math]
        CT[current_time]
        GL[glob]
        GR[grep]
        RF[read_file]
        LD[list_directory]
    end
    
    WF -->|Validate filename| WF_CHECK{Path safe?}
    WF_CHECK -->|Yes| WRITE[Write to file]
    WF_CHECK -->|No| REJECT[Reject]
    
    RC --> RUN[Execute shell]
    RUN --> TIMEOUT{60s timeout?}
    TIMEOUT -->|No| CAPTURE[Capture output]
    TIMEOUT -->|Yes| ERROR[Timeout error]
    
    M --> VALID{Valid chars?}
    VALID -->|Yes| EVAL[eval expression]
    VALID -->|No| ERR[Error]
    
    GL --> WALK[os.walk]
    WALK --> MATCH[fnmatch filter]
    MATCH --> LIMIT[Limit 200]
    
    GR --> WALK2[os.walk]
    WALK2 --> RECOMP[re.compile]
    RECOMP --> SEARCH[Line-by-line search]
    SEARCH --> LIMIT2[Limit 100]
    
    RF --> OPEN[open file]
    OPEN --> RANGE[Apply offset/limit]
    
    LD --> LIST[os.listdir]
    LIST --> FORMAT[Format with sizes]
```

## Key Features

1. **Interactive CLI**: Uses `prompt_toolkit` for rich terminal UI with history, completion, and live rendering
2. **Streaming Responses**: Real-time markdown rendering with `rich.Live`
3. **Tool System**: 8 built-in tools with optional user approval for dangerous operations
4. **Environment Context**: Automatically includes date, CWD, platform, and git info in system prompt
5. **Token Tracking**: Displays prompt/completion tokens and tokens/sec
6. **Model Selection**: Interactive model picker when connecting to custom hosts
