# Jinja Templating in Scheduler System

The scheduler system now supports Jinja templating in **all** string fields, allowing for dynamic content generation and property names using context variables.

## Overview

Jinja is a modern and designer-friendly templating language for Python, modeled after Django's templates. The scheduler system uses Jinja to allow dynamic insertion of variable values and conditional logic in instructions.

## Important Notes on Jinja Usage

### Runtime Evaluation

Jinja expressions in your instructions are **only evaluated at runtime**, not when creating or saving the schedule. This means:

1. Templates are stored in their raw form in the schedule JSON files
2. Expressions are evaluated only when the specific instruction is about to be executed
3. The current context variables at execution time are used for the evaluation

This allows the same instruction to produce different results at different times, as your context variables change.

### Type Handling

When using Jinja expressions in fields that expect non-string types (like numbers, booleans, or arrays):

1. Your JSON must use strings for the Jinja expressions (e.g., `"duration": "{{ wait_time }}"` instead of a bare number)
2. At runtime, the system will attempt to convert the result to the appropriate type
3. Numeric fields (like durations) will be converted to integers where needed
4. Boolean fields (like "dontwait") will be converted appropriately from strings like "true" or "false"

For example, if you want to use a variable for the wait duration:
```json
{
  "action": "wait",
  "duration": "{{ wait_time * 2 }}"
}
```

The JSON schema has been updated to accept strings in these numeric fields to accommodate Jinja expressions.

## Basic Usage

You can reference any context variable using the `{{ variable_name }}` syntax:

```json
{
  "action": "generate",
  "input": {
    "prompt": "A {{ animal }} in {{ environment }} style"
  }
}
```

If your context has `"animal": "cat"` and `"environment": "watercolor"`, this will evaluate to:
```
A cat in watercolor style
```

## Dynamic Property Names

You can also use Jinja templating in property names, like variable names:

```json
{
  "action": "set_var",
  "var": "{{ prefix }}_result",
  "input": {
    "value": "some value"
  }
}
```

If your context has `"prefix": "today"`, this will set the variable `today_result`.

## Available Variables

You can use any of these variable sources in your templates:

1. **Context Variables**: Any variable in the current context
2. **Exported Variables**: Any variable exported by other schedulers that this scheduler has access to

## Supported Features

### Variable Interpolation

Insert variables directly in strings:
```
{{ variable_name }}
```

### Conditional Logic

Use conditional statements:
```
{% if variable_name == "value" %}
  This will be included if the condition is true
{% else %}
  This will be included if the condition is false
{% endif %}
```

### Loops

Iterate over lists:
```
{% for item in items %}
  {{ item }}
{% endfor %}
```

### Filters

Apply transformations to variables:
```
{{ variable_name | upper }}  <!-- Convert to uppercase -->
{{ variable_name | lower }}  <!-- Convert to lowercase -->
{{ variable_name | title }}  <!-- Convert to title case -->
```

## Supported Fields

**ALL** string fields in instructions support Jinja templating, including:

- Property names like `var` in `set_var`
- Variable names in `import_var` and `export_var`
- Field values like `prompt`, `theme`, and `style`
- List items in arrays
- Duration values (which will be converted to integers)
- Action names (if not using constants)

## Examples

### Generate with Dynamic Prompt

```json
{
  "action": "generate",
  "input": {
    "prompt": "A {{ animal }} in a {{ scene }} with {{ weather }} weather"
  }
}
```

### Set Variable with Dynamic Name

```json
{
  "action": "set_var",
  "var": "{{ category }}_{{ index }}",
  "input": {
    "value": "This is the value for {{ category }} item #{{ index }}"
  }
}
```

### Import Variable with Dynamic Name

```json
{
  "action": "import_var",
  "var_name": "{{ source_prefix }}_data",
  "as": "{{ local_prefix }}_data",
  "scope": "global"
}
```

### Wait with Dynamic Duration

```json
{
  "action": "wait",
  "duration": "{{ base_wait_time * multiplier }}"
}
```

## Error Handling

If the Jinja template contains errors or references variables that don't exist, the system will:

1. Log an error
2. Return the original template string
3. Continue execution

This ensures that your schedules won't crash due to template errors.

## Implementation Details

Jinja templating is applied to the entire instruction object before it's passed to specific handlers. This means:

1. All string fields are processed with Jinja
2. All nested objects and arrays are recursively processed
3. Special type conversions (like string to int for durations) are handled automatically

## Fractional Durations

The scheduler now supports fractional durations for more precise timing:

1. **Wait Durations**: You can specify durations less than 1 minute (e.g., `0.5` for 30 seconds)
2. **Repeat Intervals**: Repeat schedules can use fractional minutes (e.g., `0.25` for 15 seconds)

Examples:

```json
// Wait for 30 seconds
{
  "action": "wait",
  "duration": 0.5
}

// Wait for a dynamic duration
{
  "action": "wait",
  "duration": "{{ base_duration / 2 }}"
}

// Repeat every 15 seconds
"repeat_schedule": {
  "every": 0.25,
  "until": "23:59"
}
```

The scheduler checks for actions multiple times per second, making sub-minute timing possible.

## Working with History Variables

The scheduler system maintains history for three instruction types: `generate`, `animate`, and `devise_prompt`. Each of these instructions can optionally store their history in a variable that you specify with the `history_var` parameter.

## History Format

Each operation type stores a standardized history entry with these common fields:
- `timestamp`: When the operation occurred (format: "YYYY-MM-DD HH:MM:SS")
- `type`: The operation type ("generation", "animation", or "devise_prompt")

Plus operation-specific fields:

**Generate History**:
```json
{
  "timestamp": "2023-08-15 14:30:45",
  "type": "generation",
  "prompt": "a cat in a garden",
  "refiner": "photorealistic",
  "workflow": "standard",
  "image_url": "images/123456.jpg"
}
```

**Animate History**:
```json
{
  "timestamp": "2023-08-15 14:35:12",
  "type": "animation",
  "prompt": "dancing gracefully",
  "image_path": "images/123456.jpg",
  "refiner": "animator",
  "animation_id": "anim_789012"
}
```

**Devise Prompt History**:
```json
{
  "timestamp": "2023-08-15 14:28:30",
  "type": "devise_prompt",
  "input": "raw input text",
  "output": "processed output text",
  "output_var": "result_var"
}
```

## Using History with Jinja

You can access history data using Jinja expressions. Here are some examples:

### 1. Reference the most recent history entry:

```json
{
  "action": "generate",
  "input": {
    "prompt": "Similar to {{ generation_history[-1].prompt }} but with more {{ style }}"
  },
  "history_var": "generation_history"
}
```

### 2. Combine multiple history entries in a prompt:

```json
{
  "action": "devise_prompt",
  "input": "Create a story about the last two images I generated: {{ generation_history[-1].prompt }} and {{ generation_history[-2].prompt }}",
  "output_var": "story",
  "history_var": "prompt_history"
}
```

### 3. Loop through history with Jinja (for complex scenarios):

```json
{
  "action": "devise_prompt",
  "input": "Recent image themes: {% for entry in generation_history[-3:] %}{{ entry.prompt }}{% if not loop.last %}, {% endif %}{% endfor %}",
  "output_var": "themes_summary",
  "history_var": "prompt_history"
}
```

### 4. Use conditionals based on history:

```json
{
  "action": "generate",
  "input": {
    "prompt": "{% if 'cat' in generation_history[-1].prompt %}A dog{% else %}A cat{% endif %} in {{ environment }}"
  },
  "history_var": "generation_history"
}
``` 