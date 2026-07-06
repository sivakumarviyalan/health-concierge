"""
Health Concierge MCP Server
============================
This module exposes three health-domain tools via FastMCP (for stdio/HTTP MCP
clients) AND as plain Python callables (so ADK agents can import them directly
without spawning a subprocess, which is unreliable on Windows).

The raw functions are defined first, then registered with FastMCP below.
"""
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("health-server")


# ─────────────────────────────────────────────────────────────────────────────
# Tool implementations — plain Python functions, importable by ADK agents
# ─────────────────────────────────────────────────────────────────────────────

def calculate_bmi(weight_kg: float, height_cm: float) -> str:
    """Calculate Body Mass Index (BMI) and provide health categorization.

    Args:
        weight_kg: Weight in kilograms.
        height_cm: Height in centimeters.
    """
    if height_cm <= 0:
        return "Height must be greater than 0."
    height_m = height_cm / 100.0
    bmi = weight_kg / (height_m * height_m)

    if bmi < 18.5:
        category = "Underweight"
    elif bmi < 25:
        category = "Normal weight"
    elif bmi < 30:
        category = "Overweight"
    else:
        category = "Obesity"

    return f"BMI: {bmi:.2f} ({category})"


def fetch_nutritional_data(query: str) -> str:
    """Fetch nutritional information (calories, macronutrients) for a given food query.

    Args:
        query: Name of the food item.
    """
    food_db = {
        "apple":          "Apple (1 medium): 95 cal, 25g Carbs, 0.5g Protein, 0.3g Fat",
        "banana":         "Banana (1 medium): 105 cal, 27g Carbs, 1.3g Protein, 0.4g Fat",
        "chicken breast": "Chicken Breast (100g): 165 cal, 0g Carbs, 31g Protein, 3.6g Fat",
        "salmon":         "Salmon (100g): 208 cal, 0g Carbs, 20g Protein, 13g Fat",
        "egg":            "Egg (1 large): 78 cal, 0.6g Carbs, 6g Protein, 5g Fat",
        "oats":           "Oats (100g cooked): 71 cal, 12g Carbs, 2.5g Protein, 1.4g Fat",
        "broccoli":       "Broccoli (100g): 34 cal, 7g Carbs, 2.8g Protein, 0.4g Fat",
        "almonds":        "Almonds (28g/1oz): 164 cal, 6g Carbs, 6g Protein, 14g Fat",
        "greek yogurt":   "Greek Yogurt (100g): 59 cal, 3.6g Carbs, 10g Protein, 0.4g Fat",
        "avocado":        "Avocado (half): 120 cal, 6g Carbs, 1.5g Protein, 11g Fat",
        "spinach":        "Spinach (100g): 23 cal, 3.6g Carbs, 2.9g Protein, 0.4g Fat",
        "brown rice":     "Brown Rice (100g cooked): 112 cal, 24g Carbs, 2.6g Protein, 0.9g Fat",
    }

    query_lower = query.lower()
    for key, val in food_db.items():
        if key in query_lower:
            return val

    return (
        f"Nutritional data for '{query}' not found. "
        "Showing estimate: approx 150 cal, 15g carbs, 10g protein, 5g fat."
    )


def log_activity(activity_type: str, duration_min: float) -> str:
    """Log workouts/physical activities and calculate estimated calories burned.

    Args:
        activity_type: Type of exercise (e.g. running, cycling, swimming, yoga).
        duration_min: Duration of activity in minutes.
    """
    met_values = {
        "running":           9.8,
        "cycling":           7.5,
        "swimming":          8.0,
        "yoga":              3.0,
        "walking":           3.5,
        "strength training": 5.0,
        "hiit":              8.5,
        "cardio":            7.0,
        "pilates":           3.5,
    }

    act_lower = activity_type.lower()
    met = 4.0  # default MET for unknown activities
    for key, val in met_values.items():
        if key in act_lower:
            met = val
            break

    # Estimate calories using MET formula for 70 kg reference person
    calories_burned = met * 3.5 * 70 / 200 * duration_min
    return (
        f"Logged {duration_min} min of {activity_type}. "
        f"Est. calories burned: {calories_burned:.1f} kcal."
    )


# ─────────────────────────────────────────────────────────────────────────────
# Register raw functions with FastMCP so they are exposed over MCP protocol
# ─────────────────────────────────────────────────────────────────────────────
mcp.tool()(calculate_bmi)
mcp.tool()(fetch_nutritional_data)
mcp.tool()(log_activity)


if __name__ == "__main__":
    mcp.run()
