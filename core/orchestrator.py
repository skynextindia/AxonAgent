import anthropic

client = anthropic.Anthropic()


def call_claude(system_prompt, user_input):
    response = client.messages.create(
        model="claude-3-5-sonnet-20241022",
        max_tokens=1500,
        temperature=0,
        system=system_prompt,
        messages=[
            {
                "role": "user",
                "content": user_input
            }
        ]
    )

    return response.content[0].text


def run_task(task):

    plan = call_claude(open("agents/planner.md").read(), task)

    analysis = call_claude(open("agents/analyzer.md").read(), plan)

    execution = call_claude(open("agents/executor.md").read(), analysis)

    validation = call_claude(open("agents/validator.md").read(), execution)

    return {
        "plan": plan,
        "analysis": analysis,
        "execution": execution,
        "validation": validation
    }


if __name__ == "__main__":
    task = input("Enter task: ")
    result = run_task(task)
    print(result)