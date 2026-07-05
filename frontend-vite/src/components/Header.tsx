import React from 'react';

/**
 * Header component — top-level branding and navigation bar.
 *
 * Displays the application title and a brief tagline.
 * Designed to be minimal; additional nav items can be added later.
 */
const Header: React.FC = () => {
  return (
    <header className="header">
      <div className="header__inner">
        <h1 className="header__title">🩺 Medical Handwriting OCR</h1>
        <p className="header__subtitle" dir="auto">
          Prescription digitisation with Arabic &amp; English support
        </p>
      </div>
    </header>
  );
};

export default Header;
