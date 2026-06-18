import { render, screen } from '@testing-library/react';
import App from './App';

// Mock child components that might use window or complex libs
jest.mock('recharts', () => ({
    ResponsiveContainer: ({ children }) => <div>{children}</div>,
    RadarChart: () => <div>RadarChart</div>,
    Radar: () => <div>Radar</div>,
    PolarGrid: () => <div>PolarGrid</div>,
    PolarAngleAxis: () => <div>PolarAngleAxis</div>,
}));

jest.mock('axios');


test('renders SAMPADA brand title', () => {
    render(<App />);
    const linkElement = screen.getByText(/SAMPADA/i);
    expect(linkElement).toBeInTheDocument();
});
