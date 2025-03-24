
import MainLayout from "@/components/layout/MainLayout";
import IntroSection from "@/components/main/IntroSection";
import HeaderSection from "@/components/main/HeaderSection";
import PromptForm from "@/components/prompt-form/PromptForm";
import ImageDisplay from "@/components/image-display/ImageDisplay";
import { Link } from "react-router-dom";

export default function Index() {
  return (
    <MainLayout>
      <div className="container mx-auto px-4 max-w-screen-xl">
        <HeaderSection />
        
        <div className="flex flex-wrap gap-2 mb-6 mt-2">
          <Link 
            to="/display" 
            className="text-blue-600 hover:text-blue-800 hover:underline text-sm flex items-center"
          >
            Display Mode
          </Link>
          <span className="text-gray-400">|</span>
          <Link 
            to="/metadata" 
            className="text-blue-600 hover:text-blue-800 hover:underline text-sm flex items-center"
          >
            Metadata Extractor
          </Link>
        </div>
        
        <IntroSection />
        <PromptForm />
        <ImageDisplay />
      </div>
    </MainLayout>
  );
}
