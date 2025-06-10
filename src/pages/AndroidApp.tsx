import { useEffect, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { Button } from '@/components/ui/button';
import { Download, AlertCircle } from 'lucide-react';
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';

const AndroidApp = () => {
  const [searchParams] = useSearchParams();
  const [showSdkDownload, setShowSdkDownload] = useState(false);
  const [isIOS, setIsIOS] = useState(false);
  
  useEffect(() => {
    // Detect iOS
    const userAgent = window.navigator.userAgent.toLowerCase();
    setIsIOS(/iphone|ipad|ipod/.test(userAgent));
    
    const target = searchParams.get('target');
    const recording = searchParams.get('recording');
    
    // Get base URL from environment variables
    const baseUrl = import.meta.env.VITE_APP_BASE_URL || 'http://185.254.136.253:8000';
    const appPackage = import.meta.env.VITE_ANDROID_APP_PACKAGE || 'com.screenmachine.audio';
    
    // Construct the deep link
    let deepLink = `${baseUrl}/androidapp`;
    if (target) deepLink += `?target=${target}`;
    if (recording) deepLink += `${target ? '&' : '?'}recording=${recording}`;
    
    // Only attempt to open Android app if not on iOS
    if (!isIOS) {
      // Try to open the app using the intent URL scheme
      window.location.href = `intent://${baseUrl.replace(/^https?:\/\//, '')}/androidapp#Intent;scheme=http;package=${appPackage};end`;
      
      // If app is not installed, show SDK download option first
      const timeoutId = setTimeout(() => {
        setShowSdkDownload(true);
      }, 2000);
      
      return () => clearTimeout(timeoutId);
    }
  }, [searchParams, isIOS]);

  const handleSdkDownload = () => {
    // Open SDK download location using WSL path
    window.location.href = 'file:///home/gjbm2/dev/screen-machine/androidapp/sdk';
  };

  // Play Store implementation coming soon
  // const handlePlayStoreRedirect = () => {
  //   const appPackage = import.meta.env.VITE_ANDROID_APP_PACKAGE || 'com.screenmachine.audio';
  //   window.location.href = `https://play.google.com/store/apps/details?id=${appPackage}`;
  // };

  if (isIOS) {
    return (
      <div className="min-h-screen flex flex-col items-center justify-center p-4">
        <Alert variant="destructive" className="max-w-md">
          <AlertCircle className="h-4 w-4" />
          <AlertTitle>iOS Not Supported</AlertTitle>
          <AlertDescription>
            The Screen Machine Audio app is currently only available for Android devices. 
            iOS support is coming soon.
          </AlertDescription>
        </Alert>
      </div>
    );
  }

  return (
    <div className="min-h-screen flex flex-col items-center justify-center p-4">
      <h1 className="text-2xl font-bold mb-4">Opening Screen Machine Audio...</h1>
      {showSdkDownload ? (
        <div className="flex flex-col items-center gap-4">
          <p className="text-gray-600 text-center mb-4">
            The app is not installed. You can download the SDK below.
          </p>
          <div className="flex flex-col items-center gap-4">
            <Button onClick={handleSdkDownload} className="flex items-center gap-2">
              <Download className="h-4 w-4" />
              Download SDK
            </Button>
            <p className="text-sm text-gray-500">
              Play Store availability coming soon
            </p>
          </div>
        </div>
      ) : (
        <p className="text-gray-600">
          If the app doesn't open automatically, you'll be redirected to the download options.
        </p>
      )}
    </div>
  );
};

export default AndroidApp; 