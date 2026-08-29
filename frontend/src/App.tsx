import { BrowserRouter as Router, Routes, Route } from 'react-router-dom';
import { Layout } from './components/layout/Layout';
import CommandCenter from './pages/CommandCenter';
import JourneyList from './pages/JourneyList';
import JourneyInvestigation from './pages/JourneyInvestigation';

function App() {
  return (
    <Router>
      <Routes>
        <Route path="/" element={<Layout />}>
          <Route index element={<CommandCenter />} />
          <Route path="journeys" element={<JourneyList />} />
          <Route path="journeys/:id" element={<JourneyInvestigation />} />
        </Route>
      </Routes>
    </Router>
  );
}

export default App;
