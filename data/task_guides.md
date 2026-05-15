EvoBench 任务指南（精简版，注入 Agent 上下文）

你的任务是完成 YatCC 编译器构造实验。 

在你的任务满分之前不得停止! 

在你的任务满分之前不得停止! 

在你的任务满分之前不得停止! 

（除了任务4，目标满分，但是越高越好） 

分数可通过已预先实现的 cmake 脚本运行得到。你可以不断运行评测以获取分数，然后针对测试样例完善编译器代码。 

你的工作目录为 /YatCC/task 。 测试样例和 cmake 脚本在 /YatCC/test/ 。脚本功能包括编译与评测。 

除了 /YatCC/test/diy-cases ，你不能修改 /YatCC/test/ ！ 

除了 /YatCC/test/diy-cases ，你不能修改 /YatCC/test/ ！ 

除了 /YatCC/test/diy-cases ，你不能修改 /YatCC/test/ ！ 

cmake 脚本编译出来了 /YatCC/build 后，再次使用脚本可能会跳过编译，或许你需要先删除 /YatCC/build 再编译。

Task 0: 环境准备





构建 task0 目标即可，不需要修改任何代码



评分：程序正常退出得 100 分

Task 1: 词法分析 (Lexer)





输入: 预处理后的 C 源码（含行标记 # linenum filename flags）



输出: 与 clang -cc1 -dump-tokens 格式一致的 token 流



每行格式: token_name 'token_value' [StartOfLine] [LeadingSpace] Loc=<file:line:col>



关键点:





行标记以 # 开头，不是源码内容，但决定文件名和行号



[StartOfLine] = token 位于行首，[LeadingSpace] = token 前有空格



最后一个 token 必须是 eof '' Loc=<file:lastline:lastcol+1>



框架:  antlr（修改 task/1/antlr/SYsULexer.g4）



评分: token 类型 60% + 位置 30% + 无关字符 10%



调试: 查看 build/test/task1/functional-0/000_main.sysu.c/answer.txt 对比标准答案

Task 2: 语法分析 (Parser)





输入: Task 1 输出的 token 流



输出: JSON 格式的 AST



框架: 使用 bison（修改 task/2/bison/par.y）或 antlr（修改 task/2/antlr/SYsUParser.g4）



评分: 逐节点对比 JSON 结构（kind/name/value 60% + type 20% + inner 20%）



调试: 查看 build/test/task2/*/answer.json 对比标准答案

Task 3: 中间代码生成 (IR Gen)





输入: JSON 格式的 AST（Task 2 输出）



输出: LLVM IR（.ll 文件）



关键文件: 只需修改 task/3/EmitIR.hpp 和 task/3/EmitIR.cpp



已实现: Json2Asg 类将 JSON 转为 ASG，基础框架已覆盖 000_main.sysu.c



评分: 生成的 IR 用 clang 编译后执行，输出和返回值与标准一致即通过



LLVM 17+ 注意: 所有指针都是 ptr 类型（Opaque Pointers）



调试: 查看 build/test/task3/*/answer.ll 对比标准答案

Task 4: 中间代码优化 (IR Opt)





输入: LLVM IR（O0 级别）



输出: 优化后的 LLVM IR



评分: score = sqrt(标准时间/学生时间) * 100（正确性优先，输出必须与标准一致）



已有基础: ConstantFolding（常量折叠）和 Mem2Reg（mem2reg）



禁止: 直接调用 LLVM 内置 Transform Pass（但可用 Analysis Pass）



建议: 先确保正确性，再追求性能

Task 5: 后端代码生成 (Asm Gen)





输入: LLVM IR



输出: RV64 汇编（.s 文件）



只需实现 4 个函数（在 task/5/EmitMIR.cpp 的 TASK 5 START 到 TASK 5 END 之间）:





emitBinary — 二元运算（add/sub/mul/div/rem/bitwise/shift）→ RV64 MIR



emitICmpInst — 整数比较 → 0/1 结果



emitLoadInst — load → LD 或 LW



emitStoreInst — store → SD 或 SW



框架已实现: 函数序言/尾声、分支跳转、函数调用、PHI 处理、GEP 地址计算



使用: emitMC 和 emitV* 辅助函数生成 MIR



虚拟寄存器: 通过 vregOf(&inst) 获取目标寄存器，emitLoadValue(operand) 获取操作数



评测: 用 qemu-riscv64-static 运行，比较输出和返回值

