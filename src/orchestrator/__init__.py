"Modular orchestrator package.\n\nImplements: SPEC-12.01-12.07\n\nThis package provides the modular orchestrator architecture:\n- core: Base RLM orchestration loop\n- intelligent: Claude-powered decision making\n- async_executor: Parallel and speculative execution\n- checkpointing: Session persistence\n- steering: User interaction support\n\nAll public classes are re-exported here for backward compatibility."

from importlib import import_module

_EXPORTS = {
    "AsyncExecutor": ("async_executor", "AsyncExecutor"),
    "AsyncRLMOrchestrator": ("async_executor", "AsyncRLMOrchestrator"),
    "BudgetChecker": ("async_executor", "BudgetChecker"),
    "ExecutionResult": ("async_executor", "ExecutionResult"),
    "PartialFailureResult": ("async_executor", "PartialFailureResult"),
    "SpeculativeExecution": ("async_executor", "SpeculativeExecution"),
    "SpeculativeResult": ("async_executor", "SpeculativeResult"),
    "CheckpointingOrchestrator": ("checkpointing", "CheckpointingOrchestrator"),
    "RLMCheckpoint": ("checkpointing", "RLMCheckpoint"),
    "OrchestrationState": ("core", "OrchestrationState"),
    "RLMOrchestrator": ("core", "RLMOrchestrator"),
    "ORCHESTRATOR_SYSTEM_PROMPT": ("intelligent", "ORCHESTRATOR_SYSTEM_PROMPT"),
    "IntelligentOrchestrator": ("intelligent", "IntelligentOrchestrator"),
    "OrchestratorConfig": ("intelligent", "OrchestratorConfig"),
    "create_orchestration_plan": ("intelligent", "create_orchestration_plan"),
    "AutoSteeringPolicy": ("steering", "AutoSteeringPolicy"),
    "InteractiveOrchestrator": ("steering", "InteractiveOrchestrator"),
    "SteeringCallback": ("steering", "SteeringCallback"),
    "SteeringDecision": ("steering", "SteeringDecision"),
    "SteeringPoint": ("steering", "SteeringPoint"),
    "SteeringPointType": ("steering", "SteeringPointType"),
    "SteeringResponse": ("steering", "SteeringResponse"),
}

__all__ = [
    # Core (SPEC-12.02)
    "OrchestrationState",
    "RLMOrchestrator",
    # Intelligent (SPEC-12.03)
    "IntelligentOrchestrator",
    "OrchestratorConfig",
    "ORCHESTRATOR_SYSTEM_PROMPT",
    "create_orchestration_plan",
    # Async (SPEC-12.04)
    "AsyncExecutor",
    "AsyncRLMOrchestrator",
    "BudgetChecker",
    "ExecutionResult",
    "PartialFailureResult",
    "SpeculativeExecution",
    "SpeculativeResult",
    # Checkpointing (SPEC-12.05)
    "CheckpointingOrchestrator",
    "RLMCheckpoint",
    # Steering (SPEC-12.06, SPEC-11.10-11.16)
    "AutoSteeringPolicy",
    "InteractiveOrchestrator",
    "SteeringCallback",
    "SteeringDecision",
    "SteeringPoint",
    "SteeringPointType",
    "SteeringResponse",
]


def __getattr__(name):
    if name not in _EXPORTS:
        raise AttributeError(name)
    module, symbol = _EXPORTS[name]
    value = getattr(import_module(f".{module}", __name__), symbol)
    globals()[name] = value
    return value


def __dir__():
    return sorted(set(globals()) | set(__all__))
