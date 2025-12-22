import React from "react";

function StatCard({ title, value, label, smallValue }) {
  return (
    <div className="stat-card-container">
      <h3 className="stat-card-title">{title}</h3>
      
      <div className={`stat-card-value ${smallValue ? 'stat-card-value-small' : 'stat-card-value-large'}`}>
        {value}
      </div>
      
      {label && (
        <div className="stat-card-label">
          {label}
        </div>
      )}
    </div>
  );
}

export default StatCard;