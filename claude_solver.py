# claude_solver.py — Gemini first, falls back to OpenAI if quota exceeded

import os
from google import genai
from openai import OpenAI
from dotenv import load_dotenv

load_dotenv()


def clean_text(task_text: str) -> str:
    cleaned = " ".join(
        word for word in task_text.split()
        if not word.startswith("<@")
    ).strip()
    return cleaned


PROMPT_TEMPLATE = """You are a task-solving assistant integrated into Slack.
When someone assigns you a task, provide a clear and actionable solution.

Format your response for Slack:
- Use *bold* for headings
- Use bullet points for steps
- Use ``` for any code blocks
- Be concise and direct

Task assigned to me: {task}"""


def solve_with_gemini(task_text: str) -> str:
    client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))
    response = client.models.generate_content(
        model="gemini-1.5-flash",
        contents=PROMPT_TEMPLATE.format(task=task_text)
    )
    return response.text


def solve_with_openai(task_text: str) -> str:
    client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    response = client.chat.completions.create(
        model="gpt-3.5-turbo",
        messages=[
            {"role": "user", "content": PROMPT_TEMPLATE.format(task=task_text)}
        ]
    )
    return response.choices[0].message.content


def solve_task(task_text: str) -> str:
    clean = clean_text(task_text)

    if not clean:
        return "I did not receive a task. Please mention me with a task description."

    # 1. Try Gemini first
    try:
        print("Trying Gemini...")
        result = solve_with_gemini(clean)
        print("Gemini responded.")
        return result

    except Exception as e:
        print(f"Gemini failed: {e} — falling back to OpenAI...")

    # 2. Fallback to OpenAI
    try:
        result = solve_with_openai(clean)
        print("OpenAI responded.")
        return result

    except Exception as e:
        print(f"OpenAI also failed: {e}")
        return f"Both Gemini and OpenAI are unavailable right now. Error: {str(e)}"