
const fs = require('fs');
const path = require('path');

// Function to recursively find all .tsx and .ts files
function findTsFiles(dir, fileList = []) {
  const files = fs.readdirSync(dir);
  
  files.forEach(file => {
    const filePath = path.join(dir, file);
    const stat = fs.statSync(filePath);
    
    if (stat.isDirectory()) {
      findTsFiles(filePath, fileList);
    } else if (file.endsWith('.tsx') || file.endsWith('.ts')) {
      fileList.push(filePath);
    }
  });
  
  return fileList;
}

// Update fetch URLs in a file
function updateApiUrls(filePath) {
  let content = fs.readFileSync(filePath, 'utf8');
  
  // Check if the file contains API calls
  if (content.includes('fetch(') && (content.includes('/generate-image') || content.includes('/images'))) {
    console.log(`Updating API URLs in ${filePath}`);
    
    // Update direct fetch calls
    content = content.replace(/fetch\s*\(\s*['"]\/generate-image['"]/g, 'fetch(\'/api/generate-image\'');
    content = content.replace(/fetch\s*\(\s*['"]\/images['"]/g, 'fetch(\'/api/images\'');
    
    // Update variable-based URLs
    content = content.replace(/const url = ['"]\/generate-image['"]/g, 'const url = \'/api/generate-image\'');
    content = content.replace(/const url = ['"]\/images['"]/g, 'const url = \'/api/images\'');
    
    fs.writeFileSync(filePath, content, 'utf8');
  }
}

// Main function
function main() {
  const srcDir = path.join(__dirname, '..', 'src');
  const tsFiles = findTsFiles(srcDir);
  
  tsFiles.forEach(updateApiUrls);
  
  console.log('API URLs updated successfully!');
}

main();
