// Utility function to convert data URI to File object
export function dataURItoFile(dataURI: string, filename: string = 'camera-photo.jpg'): File {
  const arr = dataURI.split(',');
  const mimeMatch = arr[0].match(/:(.*?);/);
  const mime = mimeMatch ? mimeMatch[1] : 'image/jpeg';
  const bstr = atob(arr[1]);
  let n = bstr.length;
  const u8arr = new Uint8Array(n);
  
  while (n--) {
    u8arr[n] = bstr.charCodeAt(n);
  }
  
  return new File([u8arr], filename, { type: mime });
}

// Check if a string is a data URI
export function isDataURI(str: string): boolean {
  return str.startsWith('data:');
} 