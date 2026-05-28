from sqlalchemy import (
    Column,
    Integer,
    Float,
    String,
    Date,
    DateTime,
    func
)

from sqlalchemy.orm import declarative_base


Base = declarative_base()


class Prediction(Base):

    __tablename__ = "predictions"

    id = Column(Integer, primary_key=True, index=True)

    input_date = Column(Date)

    store = Column(Integer)

    dept = Column(Integer)

    predicted_units = Column(Float)

    model_version = Column(String(100))

    created_at = Column(
        DateTime(timezone=True),
        server_default=func.now()
    )


class ForecastLog(Base):

    __tablename__ = "forecast_logs"

    id = Column(Integer, primary_key=True, index=True)

    forecast_date = Column(Date)

    store = Column(Integer)

    dept = Column(Integer)

    predicted_units = Column(Float)

    model_version = Column(String(100))

    created_at = Column(
        DateTime(timezone=True),
        server_default=func.now()
    )
