import { useState, useEffect } from 'react';
import { useScreens } from '@/hooks/useScreens';
import { useAnimation } from '@/hooks/useAnimation';

export default function SchedulerPage() {
    const [currentAnimation, setCurrentAnimation] = useState(null);
    const { screens } = useScreens();
    const { playAnimation, stopAnimation } = useAnimation();

    return (
        <div className="scheduler-page">
            {/* Rest of your scheduler page content */}
        </div>
    );
} 