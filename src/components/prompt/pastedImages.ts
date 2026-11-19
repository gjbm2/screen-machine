// A picture copied and pasted into the generator's prompt (Ctrl+V): PromptInput adds it like an upload.
/** The pictures on the clipboard, each with its own name (a copied picture always arrives as "image.png", and two of
 * them with one name would collide in a Combine job). */
export function pastedImages(data: DataTransfer | null, now = Date.now()): File[] {
  return Array.from(data?.items ?? [])
    .filter((item) => item.kind === "file" && item.type.startsWith("image/"))
    .map((item) => item.getAsFile())
    .filter((file): file is File => !!file)
    .map((file, i) => new File([file], `pasted-${now}-${i}.${file.type.split("/")[1] || "png"}`, { type: file.type }));
}
