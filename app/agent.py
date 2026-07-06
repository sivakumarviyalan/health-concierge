import datetime
import json
import re
import sys
from typing import Any

from google.adk.agents import LlmAgent
from google.adk.tools import AgentTool
from google.adk.workflow import Workflow, START
from google.adk.events.event import Event
from google.adk.events.request_input import RequestInput
from google.adk.agents.context import Context
from google.adk.apps import App, ResumabilityConfig
from google.genai import types

from app.config import config

# Import MCP tool functions directly (avoids Windows subprocess issue with MCPToolset).
# The mcp_server.py still defines and exposes these tools via FastMCP for any
# stdio/HTTP client that connects to it — this import just reuses the logic directly.
from app.mcp_server import calculate_bmi, fetch_nutritional_data, log_activity

# ── Specialized sub-agent: Nutrition ──────────────────────────────────────────
nutrition_agent = LlmAgent(
    name="nutrition_agent",
    model=config.model,
    instruction="""You are an expert nutritionist companion. You provide dietary advice,
meal planning, and calorie/macronutrient analysis.
Use the fetch_nutritional_data tool to look up nutritional information for foods.
Always be encouraging, friendly, and practical.""",
    description="Handles meal planning, dietary advice, nutrition, and food analysis.",
    tools=[fetch_nutritional_data],
)

# ── Specialized sub-agent: Fitness ────────────────────────────────────────────
fitness_agent = LlmAgent(
    name="fitness_agent",
    model=config.model,
    instruction="""You are a fitness and wellness companion. You design custom workout
routines, track physical activities, and manage wellness habits.
Use the calculate_bmi tool to compute BMI from weight and height.
Use the log_activity tool to log workouts and estimate calories burned.
Always ask about injuries or health concerns before suggesting intense routines.""",
    description="Handles workouts, exercise recommendations, BMI calculations, and activity tracking.",
    tools=[calculate_bmi, log_activity],
)

# ── Orchestrator ───────────────────────────────────────────────────────────────
orchestrator = LlmAgent(
    name="orchestrator",
    model=config.model,
    instruction="""You are the main Health Concierge orchestrator.
Delegate tasks to the right specialist:
- Dietary questions, meal plans, food analysis → nutrition_agent
- Workouts, BMI, activity logging → fitness_agent
- If the user wants to schedule a reminder (medication, meal, or workout):
  Extract the reminder details and append exactly this line at the END of your response:
  SCHEDULING_REQUEST: <reminder details here>
  Example: SCHEDULING_REQUEST: Daily blood pressure medication at 9 AM
  Do NOT handle scheduling yourself — the system picks it up from that line.""",
    tools=[AgentTool(nutrition_agent), AgentTool(fitness_agent)],
    description="Main orchestrator that delegates to nutrition or fitness specialists.",
)

# ── Security checkpoint node ──────────────────────────────────────────────────
def security_checkpoint(ctx: Context, node_input: Any) -> Event:
    """PII scrub, injection detection, domain safety check, and structured audit log."""
    # Extract raw text regardless of input type
    if isinstance(node_input, str):
        text = node_input
    elif hasattr(node_input, "parts") and node_input.parts:
        text = "".join(
            part.text for part in node_input.parts
            if hasattr(part, "text") and part.text
        )
    else:
        text = str(node_input)

    # 1. PII Scrubbing
    scrubbed = text
    scrubbed = re.sub(r"[\w\.-]+@[\w\.-]+\.\w+", "[EMAIL_REDACTED]", scrubbed)
    scrubbed = re.sub(
        r"\b(?:\+?1[-. ]?)?\(?([0-9]{3})\)?[-. ]?([0-9]{3})[-. ]?([0-9]{4})\b",
        "[PHONE_REDACTED]", scrubbed,
    )
    scrubbed = re.sub(r"\b(?:MRN|HI)-\d{6,8}\b", "[MEDICAL_ID_REDACTED]", scrubbed)

    # 2. Prompt-injection detection
    injection_kws = [
        "ignore safety", "ignore instructions", "bypass security",
        "system prompt", "jailbreak", "override instructions",
    ]
    is_injection = any(kw in text.lower() for kw in injection_kws)

    # 3. Domain-specific: self-harm guard
    unsafe_kws = ["commit suicide", "kill myself", "end my life", "overdose"]
    is_unsafe = any(kw in text.lower() for kw in unsafe_kws)

    # 4. Structured JSON audit log (always emitted)
    severity = "CRITICAL" if (is_injection or is_unsafe) else ("WARNING" if scrubbed != text else "INFO")
    print(json.dumps({
        "severity": severity,
        "timestamp": datetime.datetime.now().isoformat(),
        "session_id": ctx.session.id,
        "pii_detected": scrubbed != text,
        "injection_detected": is_injection,
        "unsafe_health_detected": is_unsafe,
    }))

    if is_injection or is_unsafe:
        return Event(output="Security threat detected.", route="SECURITY_EVENT")

    ctx.state["scrubbed_input"] = scrubbed
    return Event(output=scrubbed, route="__DEFAULT__")


def security_alert(ctx: Context, node_input: Any):
    msg = (
        "⚠️ Security Event: Your request violated our safety/security policy. "
        "The session has been terminated."
    )
    yield Event(content=types.Content(role="model", parts=[types.Part.from_text(text=msg)]))
    yield Event(output=msg)


# ── Orchestrator router ────────────────────────────────────────────────────────
def orchestrator_router(ctx: Context, node_input: Any) -> Event:
    """Parse orchestrator output; detect scheduling requests."""
    if isinstance(node_input, str):
        text = node_input
    elif hasattr(node_input, "parts") and node_input.parts:
        text = "".join(
            part.text for part in node_input.parts
            if hasattr(part, "text") and part.text
        )
    else:
        text = str(node_input)

    ctx.state["orchestrator_response"] = text

    if "SCHEDULING_REQUEST:" in text:
        reminder = text.split("SCHEDULING_REQUEST:", 1)[1].strip().splitlines()[0].strip()
        ctx.state["reminder_request"] = reminder
        return Event(output=reminder, route="schedule")

    return Event(output=text, route="__DEFAULT__")


# ── Human-in-the-loop: reminder scheduling ───────────────────────────────────
async def schedule_reminder_node(ctx: Context, node_input: Any):
    reminder = node_input if isinstance(node_input, str) else ctx.state.get("reminder_request", str(node_input))

    if not ctx.resume_inputs or "confirm_schedule" not in ctx.resume_inputs:
        yield RequestInput(
            interrupt_id="confirm_schedule",
            message=(
                f"✋ [Human-in-the-Loop] Authorize scheduling this reminder?\n"
                f"  → '{reminder}'\n"
                f"Type 'yes' to confirm or 'no' to cancel."
            ),
        )
        return

    response = ctx.resume_inputs.get("confirm_schedule", "").lower()
    if "yes" in response or "confirm" in response:
        msg = f"✅ Reminder scheduled: '{reminder}'"
        yield Event(output=msg, state={"reminder_status": "scheduled"})
    else:
        msg = f"❌ Reminder cancelled: '{reminder}'"
        yield Event(output=msg, state={"reminder_status": "denied"})


# ── Final output node ─────────────────────────────────────────────────────────
def final_output(ctx: Context, node_input: Any):
    if isinstance(node_input, str):
        text = node_input
    elif hasattr(node_input, "parts") and node_input.parts:
        text = "".join(
            part.text for part in node_input.parts
            if hasattr(part, "text") and part.text
        )
    else:
        text = str(node_input)

    yield Event(content=types.Content(role="model", parts=[types.Part.from_text(text=text)]))
    yield Event(output=text)


# ── Workflow graph ─────────────────────────────────────────────────────────────
# Conditional routing uses dict format: (source, {"ROUTE": target})
workflow_agent = Workflow(
    name="health_concierge_workflow",
    edges=[
        (START, security_checkpoint),
        (security_checkpoint, {"SECURITY_EVENT": security_alert, "__DEFAULT__": orchestrator}),
        (orchestrator, orchestrator_router),
        (orchestrator_router, {"schedule": schedule_reminder_node, "__DEFAULT__": final_output}),
        (schedule_reminder_node, final_output),
    ],
)

app = App(
    root_agent=workflow_agent,
    name="app",
    resumability_config=ResumabilityConfig(is_resumable=True),
)

# Alias for adk web discovery
root_agent = workflow_agent
