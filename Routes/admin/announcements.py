"""
Admin Announcements Routes
=========================

This module handles the admin routes for managing system announcements.
It provides interfaces for creating, editing, and managing announcements.

Templates Used:
-------------
- admin/announcements.html: Main announcements management page
- admin/create_announcement.html: Create new announcement form
- admin/edit_announcement.html: Edit existing announcement form

Session Requirements:
-------------------
All routes require:
- email: User's email address
- Admin status verification

Access Control:
--------------
All routes are protected by admin_required verification
"""

from flask import render_template, request, redirect, url_for, flash, session, jsonify
from datetime import datetime, timedelta
from Routes.admin import admin
from managers.authentication import admin_required
from managers.announcement_manager import AnnouncementManager
from managers.logging import webhook_log

@admin.route("/announcements")
@admin_required
def announcements():
    """
    Display announcements management page.
    
    Session Requirements:
        - email: User must be logged in
        
    Access Control:
        - User must be admin
        
    Templates:
        - admin/announcements.html: Main announcements management page
        
    Returns:
        template: admin/announcements.html with announcements data
    """
    announcements_list = AnnouncementManager.get_all_announcements()
    return render_template("admin/announcements.html", announcements=announcements_list)

@admin.route("/announcements/create", methods=['GET', 'POST'])
@admin_required
def create_announcement():
    """
    Create a new announcement.
    
    GET: Display create announcement form
    POST: Process announcement creation
    
    Session Requirements:
        - email: User must be logged in
        
    Access Control:
        - User must be admin
        
    Templates:
        - admin/create_announcement.html: Create announcement form
        
    Returns:
        template: admin/create_announcement.html (GET)
        redirect: admin/announcements (POST success)
        template: admin/create_announcement.html with errors (POST error)
    """
    if request.method == 'POST':
        try:
            # Get form data
            title = request.form.get('title', '').strip()
            message = request.form.get('message', '').strip()
            announcement_type = request.form.get('type', 'info')
            priority = int(request.form.get('priority', 1))
            
            # Date handling
            start_date = None
            end_date = None
            auto_end = False
            
            start_date_str = request.form.get('start_date', '').strip()
            end_date_str = request.form.get('end_date', '').strip()
            auto_end = bool(request.form.get('auto_end'))
            
            if start_date_str:
                start_date = datetime.fromisoformat(start_date_str)
            
            if end_date_str:
                end_date = datetime.fromisoformat(end_date_str)
            
            # Validation
            if not title:
                flash('Title is required', 'error')
                return render_template("admin/create_announcement.html")
            
            if not message:
                flash('Message is required', 'error')
                return render_template("admin/create_announcement.html")
            
            if start_date and end_date and start_date >= end_date:
                flash('Start date must be before end date', 'error')
                return render_template("admin/create_announcement.html")
            
            # Create announcement
            announcement_id = AnnouncementManager.create_announcement(
                title=title,
                message=message,
                created_by=session.get('email'),
                announcement_type=announcement_type,
                start_date=start_date,
                end_date=end_date,
                auto_end=auto_end,
                priority=priority
            )
            
            # Log the action
            webhook_log(f"**Announcement Created**\n**Admin:** {session.get('email')}\n**Title:** {title}\n**Type:** {announcement_type}")
            
            flash('Announcement created successfully!', 'success')
            return redirect(url_for('admin.announcements'))
            
        except Exception as e:
            flash(f'Error creating announcement: {str(e)}', 'error')
            return render_template("admin/create_announcement.html")
    
    return render_template("admin/create_announcement.html")

@admin.route("/announcements/<int:announcement_id>/edit", methods=['GET', 'POST'])
@admin_required
def edit_announcement(announcement_id):
    """
    Edit an existing announcement.
    
    GET: Display edit announcement form
    POST: Process announcement update
    
    Args:
        announcement_id: ID of announcement to edit
        
    Session Requirements:
        - email: User must be logged in
        
    Access Control:
        - User must be admin
        
    Templates:
        - admin/edit_announcement.html: Edit announcement form
        
    Returns:
        template: admin/edit_announcement.html with announcement data (GET)
        redirect: admin/announcements (POST success)
        template: admin/edit_announcement.html with errors (POST error)
    """
    announcement = AnnouncementManager.get_announcement_by_id(announcement_id)
    
    if not announcement:
        flash('Announcement not found', 'error')
        return redirect(url_for('admin.announcements'))
    
    if request.method == 'POST':
        try:
            # Get form data
            title = request.form.get('title', '').strip()
            message = request.form.get('message', '').strip()
            announcement_type = request.form.get('type', 'info')
            is_active = bool(request.form.get('is_active'))
            priority = int(request.form.get('priority', 1))
            
            # Date handling
            start_date = None
            end_date = None
            auto_end = False
            
            start_date_str = request.form.get('start_date', '').strip()
            end_date_str = request.form.get('end_date', '').strip()
            auto_end = bool(request.form.get('auto_end'))
            
            if start_date_str:
                start_date = datetime.fromisoformat(start_date_str)
            
            if end_date_str:
                end_date = datetime.fromisoformat(end_date_str)
            
            # Validation
            if not title:
                flash('Title is required', 'error')
                return render_template("admin/edit_announcement.html", announcement=announcement)
            
            if not message:
                flash('Message is required', 'error')
                return render_template("admin/edit_announcement.html", announcement=announcement)
            
            if start_date and end_date and start_date >= end_date:
                flash('Start date must be before end date', 'error')
                return render_template("admin/edit_announcement.html", announcement=announcement)
            
            # Update announcement
            success = AnnouncementManager.update_announcement(
                announcement_id=announcement_id,
                title=title,
                message=message,
                announcement_type=announcement_type,
                is_active=is_active,
                start_date=start_date,
                end_date=end_date,
                auto_end=auto_end,
                priority=priority
            )
            
            if success:
                # Log the action
                webhook_log(f"**Announcement Updated**\n**Admin:** {session.get('email')}\n**ID:** {announcement_id}\n**Title:** {title}")
                
                flash('Announcement updated successfully!', 'success')
                return redirect(url_for('admin.announcements'))
            else:
                flash('Error updating announcement', 'error')
                return render_template("admin/edit_announcement.html", announcement=announcement)
            
        except Exception as e:
            flash(f'Error updating announcement: {str(e)}', 'error')
            return render_template("admin/edit_announcement.html", announcement=announcement)
    
    return render_template("admin/edit_announcement.html", announcement=announcement)

@admin.route("/announcements/<int:announcement_id>/toggle", methods=['POST'])
@admin_required
def toggle_announcement(announcement_id):
    """
    Toggle announcement active status.
    
    Args:
        announcement_id: ID of announcement to toggle
        
    Session Requirements:
        - email: User must be logged in
        
    Access Control:
        - User must be admin
        
    Returns:
        jsonify: Success/error status
    """
    try:
        announcement = AnnouncementManager.get_announcement_by_id(announcement_id)
        
        if not announcement:
            return jsonify({'success': False, 'message': 'Announcement not found'})
        
        # Toggle active status
        new_status = not announcement['is_active']
        success = AnnouncementManager.update_announcement(
            announcement_id=announcement_id,
            is_active=new_status
        )
        
        if success:
            action = "activated" if new_status else "deactivated"
            webhook_log(f"**Announcement {action.title()}**\n**Admin:** {session.get('email')}\n**ID:** {announcement_id}\n**Title:** {announcement['title']}")
            
            return jsonify({
                'success': True, 
                'message': f'Announcement {action} successfully',
                'is_active': new_status
            })
        else:
            return jsonify({'success': False, 'message': 'Error updating announcement'})
            
    except Exception as e:
        return jsonify({'success': False, 'message': f'Error: {str(e)}'})

@admin.route("/announcements/<int:announcement_id>/delete", methods=['POST'])
@admin_required
def delete_announcement(announcement_id):
    """
    Delete an announcement.
    
    Args:
        announcement_id: ID of announcement to delete
        
    Session Requirements:
        - email: User must be logged in
        
    Access Control:
        - User must be admin
        
    Returns:
        jsonify: Success/error status
    """
    try:
        announcement = AnnouncementManager.get_announcement_by_id(announcement_id)
        
        if not announcement:
            return jsonify({'success': False, 'message': 'Announcement not found'})
        
        success = AnnouncementManager.delete_announcement(announcement_id)
        
        if success:
            webhook_log(f"**Announcement Deleted**\n**Admin:** {session.get('email')}\n**ID:** {announcement_id}\n**Title:** {announcement['title']}")
            
            return jsonify({'success': True, 'message': 'Announcement deleted successfully'})
        else:
            return jsonify({'success': False, 'message': 'Error deleting announcement'})
            
    except Exception as e:
        return jsonify({'success': False, 'message': f'Error: {str(e)}'})

@admin.route("/announcements/<int:announcement_id>/end", methods=['POST'])
@admin_required
def end_announcement(announcement_id):
    """
    Manually end an announcement.
    
    Args:
        announcement_id: ID of announcement to end
        
    Session Requirements:
        - email: User must be logged in
        
    Access Control:
        - User must be admin
        
    Returns:
        jsonify: Success/error status
    """
    try:
        announcement = AnnouncementManager.get_announcement_by_id(announcement_id)
        
        if not announcement:
            return jsonify({'success': False, 'message': 'Announcement not found'})
        
        success = AnnouncementManager.end_announcement(announcement_id)
        
        if success:
            webhook_log(f"**Announcement Ended**\n**Admin:** {session.get('email')}\n**ID:** {announcement_id}\n**Title:** {announcement['title']}")
            
            return jsonify({'success': True, 'message': 'Announcement ended successfully'})
        else:
            return jsonify({'success': False, 'message': 'Error ending announcement'})
            
    except Exception as e:
        return jsonify({'success': False, 'message': f'Error: {str(e)}'})
