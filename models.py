class ODRequest(Base):
    __tablename__ = "od_requests"
    id = Column(Integer, primary_key=True)
    student_id = Column(Integer, ForeignKey("students.id"), nullable=False)
    teacher_id = Column(Integer, ForeignKey("teachers.id"), nullable=False)  # who approves
    reason = Column(Text, nullable=False)
    from_date = Column(DateTime, nullable=False)
    to_date = Column(DateTime, nullable=False)
    status = Column(String, default="pending")   # pending / approved / rejected
    proof_url = Column(String)                    # optional OneDrive link
    created_at = Column(DateTime, default=datetime.utcnow)
