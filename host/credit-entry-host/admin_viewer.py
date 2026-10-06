import tkinter as tk
from tkinter import ttk, messagebox
import sqlite3
import os

# Default Database Paths (can be updated by the user)
MARAKI_DB_PATH = os.path.join(os.path.dirname(__file__), '..', 'mrk-host', 'maraki-db.db')
CREDIT_DB_PATH = os.path.join(os.path.dirname(__file__), 'credit_entry.sqlite3')

class DatabaseViewer:
    def __init__(self, parent, db_path, name):
        self.parent = parent
        self.db_path = db_path
        self.name = name
        
        self.frame = ttk.Frame(parent)
        self.parent.add(self.frame, text=name)
        
        self.setup_ui()
        self.load_tables()

    def setup_ui(self):
        # Top Controls
        control_frame = ttk.Frame(self.frame)
        control_frame.pack(fill=tk.X, padx=5, pady=5)
        
        ttk.Label(control_frame, text="Table:").pack(side=tk.LEFT, padx=5)
        self.table_var = tk.StringVar()
        self.table_cb = ttk.Combobox(control_frame, textvariable=self.table_var, state="readonly")
        self.table_cb.pack(side=tk.LEFT, padx=5)
        self.table_cb.bind("<<ComboboxSelected>>", self.on_table_select)
        
        ttk.Label(control_frame, text="Search:").pack(side=tk.LEFT, padx=5)
        self.search_var = tk.StringVar()
        self.search_entry = ttk.Entry(control_frame, textvariable=self.search_var, width=30)
        self.search_entry.pack(side=tk.LEFT, padx=5)
        self.search_entry.bind("<Return>", lambda e: self.load_data())
        
        ttk.Button(control_frame, text="Filter", command=self.load_data).pack(side=tk.LEFT, padx=5)
        ttk.Button(control_frame, text="Refresh DB", command=self.load_tables).pack(side=tk.LEFT, padx=5)
        
        # Treeview for Data
        self.tree_frame = ttk.Frame(self.frame)
        self.tree_frame.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        
        self.tree = ttk.Treeview(self.tree_frame, show='headings')
        self.vsb = ttk.Scrollbar(self.tree_frame, orient="vertical", command=self.tree.yview)
        self.hsb = ttk.Scrollbar(self.tree_frame, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=self.vsb.set, xscrollcommand=self.hsb.set)
        
        self.tree.grid(column=0, row=0, sticky='nsew')
        self.vsb.grid(column=1, row=0, sticky='ns')
        self.hsb.grid(column=0, row=1, sticky='ew')
        self.tree_frame.grid_columnconfigure(0, weight=1)
        self.tree_frame.grid_rowconfigure(0, weight=1)

    def load_tables(self):
        if not os.path.exists(self.db_path):
            self.table_cb['values'] = []
            return
            
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
            tables = [row[0] for row in cursor.fetchall()]
            conn.close()
            
            self.table_cb['values'] = tables
            if tables:
                self.table_cb.current(0)
                self.on_table_select()
        except Exception as e:
            messagebox.showerror("Database Error", f"Failed to read {self.name}:\n{e}")

    def on_table_select(self, event=None):
        self.search_var.set("")
        self.load_data()

    def load_data(self):
        table = self.table_var.get()
        if not table or not os.path.exists(self.db_path):
            return
            
        search_query = self.search_var.get().strip()
        
        # Clear existing data
        for col in self.tree.get_children():
            self.tree.delete(col)
            
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            # Get columns
            cursor.execute(f"PRAGMA table_info({table})")
            columns = [info[1] for info in cursor.fetchall()]
            
            self.tree['columns'] = columns
            for col in columns:
                self.tree.heading(col, text=col)
                self.tree.column(col, width=100, minwidth=50)
                
            # Fetch data
            if search_query:
                # Build a search condition for all columns
                conditions = " OR ".join([f'"{col}" LIKE ?' for col in columns])
                params = [f"%{search_query}%"] * len(columns)
                cursor.execute(f"SELECT * FROM {table} WHERE {conditions} LIMIT 1000", params)
            else:
                cursor.execute(f"SELECT * FROM {table} LIMIT 1000")
                
            rows = cursor.fetchall()
            for row in rows:
                self.tree.insert("", tk.END, values=row)
                
            conn.close()
        except Exception as e:
            messagebox.showerror("Data Error", f"Failed to load data for {table}:\n{e}")

class AdminViewerApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("CES Admin Viewer")
        self.geometry("1000x600")
        
        self.notebook = ttk.Notebook(self)
        self.notebook.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        
        self.maraki_viewer = DatabaseViewer(self.notebook, MARAKI_DB_PATH, "Maraki DB")
        
        # In case the user is running the client and host on the same machine, 
        # try the client db path if the host one doesn't exist
        fallback_credit_path = "C:/client-data/credit-entry-data/transactions.sqlite3"
        credit_path = CREDIT_DB_PATH
        if not os.path.exists(credit_path) and os.path.exists(fallback_credit_path):
            credit_path = fallback_credit_path
            
        self.credit_viewer = DatabaseViewer(self.notebook, credit_path, "Credit Entry DB")

if __name__ == "__main__":
    app = AdminViewerApp()
    app.mainloop()
