// The bucket grid shows a favourite twice and gives the copy in its date group the id `<file>#dup` (dnd-kit needs
// unique ids). That id is the grid's own; every API call that names a bucket file sends the real filename.
export const realFilename = (filename: string): string => filename.replace(/#dup$/, '');
