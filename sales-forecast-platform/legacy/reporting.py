from src.insights import generate_forecast_insights


def generate_forecast_report(store: int | None = None, dept: int | None = None):
    result = generate_forecast_insights(store=store, dept=dept)

    summary = result.get("summary", "No summary available.")
    insights = result.get("insights", [])
    stats = result.get("stats", {})

    if not stats:
        return "No forecast report available yet."

    trend = stats.get("trend", "stable")
    avg_forecast = stats.get("average_forecast", 0)
    total_forecast = stats.get("total_forecast", 0)
    records = stats.get("records", 0)

    report = f"""
Forecast Report

Scope:
- Store: {store if store is not None else 'All'}
- Department: {dept if dept is not None else 'All'}

Executive Summary:
{summary}

Details:
- Forecast records analyzed: {records}
- Average forecasted units: {avg_forecast:,.2f}
- Total forecasted units: {total_forecast:,.2f}
- Trend direction: {trend}

Key Insights:
"""

    for item in insights:
        report += f"- {item}\n"

    if trend == "upward":
        report += "\nRecommendation:\n- Consider preparing for higher expected demand in the coming forecast horizon.\n"
    elif trend == "downward":
        report += "\nRecommendation:\n- Monitor inventory and demand closely as the forecast suggests softening demand.\n"
    else:
        report += "\nRecommendation:\n- Demand appears stable. Maintain normal monitoring and replenishment.\n"

    return report.strip()