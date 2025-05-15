# Display Modes

The scheduler system supports four display modes when using the `display` instruction. This document explains what each mode does and how to use them.

## Available Display Modes

| Mode | Description |
|------|-------------|
| `Next` | Displays the next image in the favorites sequence |
| `Previous` | Displays the previous image in the favorites sequence |
| `Random` | Displays a randomly selected image from the favorites |
| `Blank` | Displays a blank screen |

## How Display Modes Work

### Next Mode

The `Next` mode moves forward through your favorites list. It finds the currently displayed image in the favorites list and shows the next one. If it reaches the end of the list, it wraps around to the beginning.

Example:
```json
{
  "action": "display",
  "show": "Next",
  "silent": false
}
```

### Previous Mode

The `Previous` mode moves backward through your favorites list. It finds the currently displayed image in the favorites list and shows the previous one. If it reaches the beginning of the list, it wraps around to the end.

Example:
```json
{
  "action": "display",
  "show": "Previous",
  "silent": false
}
```

### Random Mode

The `Random` mode selects a random image from your favorites list and displays it. This is useful for creating shuffle-like behavior.

Example:
```json
{
  "action": "display",
  "show": "Random",
  "silent": false
}
```

### Blank Mode

The `Blank` mode displays a blank (black) screen. This is useful for clearing the display or creating intervals between images.

Example:
```json
{
  "action": "display",
  "show": "Blank",
  "silent": true
}
```

## The `silent` Parameter

All display modes accept an optional `silent` parameter:

- When `silent` is `false` (default), an overlay with information about the image is displayed.
- When `silent` is `true`, the image is displayed without an overlay.

## Creating a Slideshow

You can create a slideshow by combining display modes with sleep and event instructions. See the example in `examples/display_example.json` for a complete slideshow implementation.

Basic slideshow example:
```json
{
  "action": "display",
  "show": "Next",
  "silent": false
},
{
  "action": "sleep",
  "duration": 30
},
{
  "action": "display",
  "show": "Next",
  "silent": false
}
```

This will show the next image, wait 30 seconds, and then show the next image again. 